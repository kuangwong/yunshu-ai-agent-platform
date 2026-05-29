import os
import os.path
import logging
import asyncio
import subprocess
import psutil
import json
import re
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

def validate_safe_path(path: str) -> str:
    """
    安全校验：限制物理读写仅允许在合法的 data 安全目录下，防止路径穿越 (..)。
    """
    abs_path = os.path.abspath(path)
    
    # 获得当前项目的绝对路径和典型的 docker 挂载路径
    project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))))
    
    # 合法的目录前缀列表
    allowed_prefixes = [
        os.path.join(project_root, "data"),
        "/app/data"
    ]
    
    # 额外兼容：支持直接操作相对路径的 data 目录
    allowed_prefixes.append(os.path.abspath("data"))
    
    # 额外允许技能物理目录，以便 create_skills 等工具能正常操作宿主机 ~/.agent/skills 或容器 /app/data/skills
    try:
        from app.core.config import settings
        if getattr(settings, "SKILLS_DIR", None):
            allowed_prefixes.append(os.path.abspath(settings.SKILLS_DIR))
    except Exception:
        pass
    
    is_safe = False
    for prefix in allowed_prefixes:
        try:
            if os.path.commonpath([abs_path, prefix]) == prefix:
                is_safe = True
                break
        except ValueError:
            continue
            
    if not is_safe:
        raise ValueError(f"安全拦截：路径越界！仅允许访问 data 及其子目录下的文件。当前请求路径为 {path}")
        
    return abs_path


def _validate_skill_id(skill_id: str) -> str:
    if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
        raise ValueError("非法技能 ID 格式。禁止包含路径分隔符或穿越符。")
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill_id):
        raise ValueError("非法技能 ID 格式。仅允许包含英文字母、数字、中划线和下划线。")
    return skill_id


def _parse_skill_frontmatter(skill_id: str, skill_md_path: str) -> dict:
    meta = {
        "id": skill_id,
        "name": skill_id,
        "description": "暂无技能描述",
    }
    if not os.path.exists(skill_md_path):
        return meta

    try:
        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(8192)
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return meta
        frontmatter = match.group(1)
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
        if name_match:
            meta["name"] = name_match.group(1).strip().strip("\"'")
        if desc_match:
            meta["description"] = desc_match.group(1).strip().strip("\"'")
    except Exception as e:
        logger.warning("[Skills] Failed to parse frontmatter for %s: %s", skill_id, e)
    return meta


@tool
def list_available_skills() -> str:
    """
    列出当前平台技能库中可用的技能摘要，供智能体按 name/description 判断是否需要读取具体技能。
    只返回技能 ID、名称和描述，不读取完整 SKILL.md。
    """
    try:
        from app.core.config import settings

        skills_dir = settings.SKILLS_DIR
        if not os.path.exists(skills_dir):
            return "[]"

        skills = []
        for item in sorted(os.listdir(skills_dir)):
            if item.startswith("."):
                continue
            item_path = os.path.join(skills_dir, item)
            if not os.path.isdir(item_path):
                continue
            try:
                _validate_skill_id(item)
            except ValueError:
                logger.warning("[Skills] Ignored invalid skill directory name: %s", item)
                continue
            skill_md_path = os.path.join(item_path, "SKILL.md")
            skills.append(_parse_skill_frontmatter(item, skill_md_path))

        return json.dumps(skills, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"错误：列出技能失败，原因: {str(e)}"


@tool
def read_skill_instruction(skill_id: str) -> str:
    """
    读取指定技能的 SKILL.md 指令内容。调用前应先使用 list_available_skills 确认技能存在且适用。

    Args:
        skill_id: 技能唯一 ID，仅允许英文、数字、中划线及下划线。
    """
    try:
        from app.core.config import settings

        safe_skill_id = _validate_skill_id(skill_id)
        skills_dir = os.path.abspath(settings.SKILLS_DIR)
        skill_md_path = os.path.abspath(os.path.join(skills_dir, safe_skill_id, "SKILL.md"))
        if os.path.commonpath([skills_dir, skill_md_path]) != skills_dir:
            return "错误：技能路径越界。"
        if not os.path.exists(skill_md_path):
            return f"错误：技能 {safe_skill_id} 不存在或缺少 SKILL.md。"

        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(262144)
        return f"[技能读取成功: {safe_skill_id}/SKILL.md]\n{content}"
    except ValueError as e:
        return f"错误：非法技能 ID，{str(e)}"
    except Exception as e:
        return f"错误：读取技能失败，原因: {str(e)}"

@tool
def read_local_file(path: str, offset: int = 0, limit: int = 262144, tail: bool = False) -> str:
    """
    以分页、截断、倒序（Tail）的方式安全读取本地文件。
    限制单次读取的最大字节数以防止大模型 Token 暴涨，只允许读取 data 目录下的安全沙箱文件。
    
    Args:
        path: 目标文件路径 (如 data/uploads/logs.txt)。
        offset: 分页读取的开始字节偏移量。默认为 0。
        limit: 单次读取的最大字节长度限制，默认 256KB。
        tail: 为 True 时，直接读取文件末尾 limit 长度的内容。
    """
    try:
        abs_path = validate_safe_path(path)
        if not os.path.exists(abs_path):
            return f"错误：文件 {path} 不存在。"
        if os.path.isdir(abs_path):
            return f"错误：{path} 是一个目录，请使用 directory_tree_navigator 工具进行查看。"

        file_size = os.path.getsize(abs_path)
        
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            if tail:
                if file_size > limit:
                    f.seek(file_size - limit)
                content = f.read()
                return f"[Tail 读取成功 ({file_size} 字节中的最后 {len(content.encode('utf-8'))} 字节)]\n{content}"
            else:
                f.seek(offset)
                content = f.read(limit)
                is_eof = (offset + len(content.encode('utf-8')) >= file_size)
                return f"[分页读取成功 (文件总长 {file_size} 字节，本次读取偏移量 {offset}，读取 {len(content.encode('utf-8'))} 字节，EOF={is_eof})]\n{content}"
    except Exception as e:
        return f"读取文件失败: {str(e)}"

@tool
def write_local_file(path: str, content: str) -> str:
    """
    直接在安全沙箱 data 目录下写入或覆盖指定文件，若父级目录不存在将自动创建。
    
    Args:
        path: 物理写入目标路径 (如 data/skills/my_skill.py)。
        content: 写入文件的完整文本内容。
    """
    try:
        abs_path = validate_safe_path(path)
        
        # 自动创建父级目录
        dir_name = os.path.dirname(abs_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        return f"物理写入成功！路径：{path}，写入大小：{len(content.encode('utf-8'))} 字节。"
    except Exception as e:
        return f"写入文件失败: {str(e)}"

@tool
async def execute_system_command(command: str) -> str:
    """
    在系统物理环境或本地沙盒中执行指定的 shell 命令，强制施加默认 30 秒的超时限制与标准输出的长度截断。
    
    Args:
        command: 要执行的 Shell 命令。
    """
    try:
        # 高危命令简单拦截
        forbidden_keywords = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot"]
        if any(kw in command for kw in forbidden_keywords):
            return f"安全拦截：该命令包含高危操作，被禁止执行。"

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except:
                pass
            return f"错误：命令执行超时（最大限制 30 秒）。已强制终止命令进程。"

        stdout_str = stdout.decode("utf-8", errors="ignore")
        stderr_str = stderr.decode("utf-8", errors="ignore")
        
        max_output_len = 102400  # 100KB
        truncated = False
        if len(stdout_str) > max_output_len:
            stdout_str = stdout_str[:max_output_len] + "\n...[部分输出已截断]..."
            truncated = True
            
        result = []
        if stdout_str:
            result.append(f"--- [stdout] ---\n{stdout_str}")
        if stderr_str:
            result.append(f"--- [stderr] ---\n{stderr_str}")
            
        if not result:
            return f"命令执行成功，无任何控制台输出。ExitCode={proc.returncode}"
            
        return "\n".join(result) + (f"\nExitCode={proc.returncode}" if not truncated else "")
    except Exception as e:
        return f"命令执行异常: {str(e)}"

@tool
def manage_system_process(action: str, pid: int = None) -> str:
    """
    管理和查看当前系统的运行进程，防止误杀核心 Uvicorn/FastAPI 主进程以及开发守护进程。
    
    Args:
        action: 只能是 "list" (列出进程) 或 "kill" (终止进程)。
        pid: 当 action="kill" 时，必须提供指定的目标 PID。
    """
    try:
        current_pid = os.getpid()
        parent_pids = []
        try:
            p = psutil.Process(current_pid)
            while p.parent():
                parent_pids.append(p.parent().pid)
                p = p.parent()
        except:
            pass
        
        protected_pids = set([current_pid, 1] + parent_pids)

        action = action.lower()
        if action == "list":
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info']):
                try:
                    info = proc.info
                    pid_val = info['pid']
                    is_protected = pid_val in protected_pids
                    processes.append({
                        "pid": pid_val,
                        "name": info['name'],
                        "username": info['username'],
                        "cpu": f"{proc.cpu_percent()}%",
                        "memory": f"{round(info['memory_info'].rss / (1024 * 1024), 2)} MB",
                        "status": "核心受保护" if is_protected else "可控"
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            lines = [f"{'PID':<8} {'进程名':<25} {'CPU占用':<10} {'物理内存':<12} {'状态':<10}"]
            lines.append("-" * 70)
            for p in sorted(processes, key=lambda x: x['pid']):
                lines.append(f"{p['pid']:<8} {p['name']:<25} {p['cpu']:<10} {p['memory']:<12} {p['status']:<10}")
            return "\n".join(lines)
            
        elif action == "kill":
            if pid is None:
                return "错误：当 action='kill' 时，必须指定目标 pid。"
                
            if pid in protected_pids:
                return f"安全越权拦截：PID {pid} 是系统核心 Web 服务或守护进程，受到强制保护，禁止终止！"
                
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                return f"成功终止进程 PID {pid} ({proc.name()})。"
            except psutil.NoSuchProcess:
                return f"错误：未查找到 PID {pid} 的进程。"
            except psutil.AccessDenied:
                return f"错误：权限不足，无法终止 PID {pid} 进程。"
        else:
            return "错误：不支持的 action 参数。仅支持 'list' 或 'kill'。"
    except Exception as e:
        return f"进程管理操作异常: {str(e)}"


@tool
def create_skills(skill_id: str, name: str, description: str, skill_md_content: str) -> str:
    """
    根据当前的聊天会话内容与用户意图，按照 Anthropic Skills 生态规范在 skills 目录下物理创建或初始化一个智能体技能（Skill）。
    这会创建一个技能文件夹，并强制写入包含 Frontmatter 触发定义的 SKILL.md 规范守则文件。
    
    Args:
        skill_id: 技能唯一英文标识 (如 translate-helper)，仅支持英文、数字、中划线及下划线。
        name: 技能的人类可读名称 (如 多语言智能翻译官)。
        description: 技能的核心应用场景与触发场景说明。
        skill_md_content: 该技能核心 SKILL.md 文件的完整 Markdown 文本。
                          必须符合规范：开头需包含 YAML Frontmatter 描述（以 --- 包裹，内含 name: <名称> 与 description: <描述> 属性），随后为 imperative 操作指令。
    """
    try:
        import re
        if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
            return "错误：非法技能 ID 格式。禁止包含路径分隔符或穿越符。"
        if not re.match(r"^[a-zA-Z0-9_-]+$", skill_id):
            return "错误：技能 ID 仅允许包含英文字母、数字、中划线和下划线。"
            
        from app.core.config import settings
        skills_dir = settings.SKILLS_DIR
        if not skills_dir:
            return "错误：未配置 SKILLS_DIR 目录。"
            
        # 拼接物理路径
        skill_path = os.path.join(skills_dir, skill_id)
        
        # 安全防御拦截
        validate_safe_path(skill_path)
        
        os.makedirs(skill_path, exist_ok=True)
        skill_md_path = os.path.join(skill_path, "SKILL.md")
        
        # 写入物理磁盘
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(skill_md_content)
            
        return f"成功：技能 {skill_id} 创建成功。SKILL.md 规范文件已物理写入路径 {skill_md_path}。"
    except Exception as e:
        return f"错误：创建技能失败，原因: {str(e)}"
