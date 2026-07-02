<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[9999] flex justify-end bg-gray-900/40 backdrop-blur-sm"
    @click.self="emit('close')"
  >
    <div class="w-full max-w-3xl h-full bg-white shadow-2xl flex flex-col animate-slide-in-right">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between shrink-0">
        <div>
          <h2 class="text-lg font-bold text-gray-900">同步第三方用户</h2>
          <p class="text-xs text-gray-400 mt-0.5">配置数据源、字段映射，支持定时与手动同步</p>
        </div>
        <button @click="emit('close')" class="p-2 hover:bg-gray-100 rounded-lg transition-colors">
          <XMarkIcon class="w-5 h-5 text-gray-500" />
        </button>
      </div>

      <!-- Body: single page -->
      <div class="flex-1 overflow-y-auto p-6 space-y-6">
        <!-- Master switch -->
        <div class="flex items-center justify-between p-4 bg-gray-50 rounded-xl border border-gray-100">
          <div>
            <p class="text-sm font-medium text-gray-700">启用第三方用户同步</p>
            <p class="text-xs text-gray-400">关闭后下方配置不可用，定时任务也不会执行</p>
          </div>
          <Switch v-model="config.enabled" />
        </div>

        <div
          class="space-y-6 transition-opacity"
          :class="config.enabled ? '' : 'opacity-50 pointer-events-none select-none'"
        >
          <!-- 数据源与表映射 -->
          <section class="space-y-4">
            <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">数据源与表映射</h3>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-2">选择数据源</label>
              <select
                v-model.number="config.connection_config_id"
                class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none disabled:bg-gray-50"
                :disabled="!config.enabled"
                @change="onDatasourceChange"
              >
                <option :value="null" disabled>请选择已配置的数据源</option>
                <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
                  {{ ds.name }} ({{ ds.db_type }} / {{ ds.database_name }})
                </option>
              </select>
              <p class="text-xs text-gray-400 mt-1">数据源在「数据源管理」中维护</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-2">用户表</label>
              <select
                v-model="config.table_name"
                class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none disabled:bg-gray-50"
                :disabled="!config.enabled || !config.connection_config_id || loadingTables"
                @change="onTableChange"
              >
                <option value="" disabled>{{ loadingTables ? '加载中...' : '请选择用户表' }}</option>
                <option v-for="t in tables" :key="t.name" :value="t.name">
                  {{ t.name }}<template v-if="t.type"> ({{ t.type }})</template>
                </option>
              </select>
            </div>

            <div v-if="config.table_name" class="grid grid-cols-1 gap-3">
              <div
                v-for="field in mappingFields"
                :key="field.key"
                class="flex items-center gap-3"
              >
                <div class="w-28 shrink-0">
                  <span class="text-sm font-medium text-gray-700">{{ field.label }}</span>
                  <span v-if="field.required" class="text-red-500 ml-0.5">*</span>
                </div>
                <select
                  v-model="config.field_map[field.key]"
                  class="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none disabled:bg-gray-50"
                  :disabled="!config.enabled || loadingColumns"
                >
                  <option value="">{{ loadingColumns ? '加载中...' : '请选择源字段' }}</option>
                  <option v-for="col in columns" :key="col.name" :value="col.name">{{ col.name }}</option>
                </select>
              </div>
            </div>

            <p class="text-xs text-gray-500 bg-amber-50 border border-amber-100 rounded-lg p-3">
              第三方用户 ID 将直接写入本地用户表主键；若本地已存在相同 ID 则跳过，不更新已有用户。新用户角色默认为「普通用户」，API Key 自动生成。
            </p>
          </section>

          <!-- 定时同步 -->
          <section class="space-y-3">
            <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">定时同步</h3>
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="opt in scheduleOptions"
                :key="opt.value"
                type="button"
                :disabled="!config.enabled"
                @click="config.schedule = opt.value"
                class="px-4 py-3 rounded-xl border text-sm font-medium transition-all text-left disabled:cursor-not-allowed"
                :class="config.schedule === opt.value ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'"
              >
                <div>{{ opt.label }}</div>
                <div class="text-xs font-normal mt-0.5 opacity-70">{{ opt.desc }}</div>
              </button>
            </div>
          </section>

          <!-- 手动同步 -->
          <section class="space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">手动同步</h3>
              <button
                type="button"
                @click="loadPreview"
                :disabled="!config.enabled || loadingPreview || !canPreview"
                class="text-sm text-indigo-600 hover:text-indigo-800 font-medium disabled:opacity-50"
              >
                刷新预览
              </button>
            </div>

            <div class="flex items-center gap-2">
              <input
                v-model="hideSynced"
                type="checkbox"
                id="hide-synced-tp"
                :disabled="!config.enabled"
                class="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              <label for="hide-synced-tp" class="text-sm text-gray-600">隐藏已同步用户</label>
            </div>

            <div v-if="loadingPreview" class="h-40 flex items-center justify-center text-gray-400">
              <div class="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
            </div>

            <div v-else-if="!canPreview" class="h-32 flex items-center justify-center text-gray-400 text-sm bg-gray-50 rounded-xl border border-dashed border-gray-200">
              请先完成数据源与字段映射并保存配置
            </div>

            <div v-else-if="filteredPreview.length === 0" class="h-32 flex items-center justify-center text-gray-400 text-sm bg-gray-50 rounded-xl">
              暂无待同步用户
            </div>

            <div v-else class="border border-gray-200 rounded-xl overflow-hidden max-h-64 overflow-y-auto">
              <table class="w-full text-sm">
                <thead class="bg-gray-50 text-gray-500 sticky top-0">
                  <tr>
                    <th class="px-3 py-2 text-left w-10">
                      <input
                        type="checkbox"
                        :checked="isAllSelected"
                        :disabled="!config.enabled"
                        @change="toggleSelectAll"
                        class="rounded border-gray-300 text-indigo-600"
                      />
                    </th>
                    <th class="px-3 py-2 text-left">ID</th>
                    <th class="px-3 py-2 text-left">用户名</th>
                    <th class="px-3 py-2 text-left">真实姓名</th>
                    <th class="px-3 py-2 text-left">备注</th>
                    <th class="px-3 py-2 text-left">状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="user in filteredPreview"
                    :key="user.id"
                    class="border-t border-gray-100 hover:bg-gray-50"
                    :class="user.is_synced ? 'opacity-50' : 'cursor-pointer'"
                    @click="config.enabled && !user.is_synced && toggleSelect(user.id)"
                  >
                    <td class="px-3 py-2">
                      <input
                        type="checkbox"
                        :checked="selectedIds.includes(user.id)"
                        :disabled="!config.enabled || user.is_synced"
                        @click.stop
                        @change="toggleSelect(user.id)"
                        class="rounded border-gray-300 text-indigo-600"
                      />
                    </td>
                    <td class="px-3 py-2 font-mono text-xs">{{ user.id }}</td>
                    <td class="px-3 py-2">{{ user.user_name }}</td>
                    <td class="px-3 py-2">{{ user.real_name || '-' }}</td>
                    <td class="px-3 py-2 text-gray-500 truncate max-w-[120px]">{{ user.remark || '-' }}</td>
                    <td class="px-3 py-2">
                      <span
                        class="text-xs px-2 py-0.5 rounded-full"
                        :class="user.is_synced ? 'bg-gray-100 text-gray-500' : 'bg-green-50 text-green-700'"
                      >
                        {{ user.is_synced ? '已存在' : '待同步' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-gray-100 flex items-center justify-between shrink-0 bg-gray-50">
        <button
          type="button"
          @click="saveConfig"
          :disabled="saving"
          class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          {{ saving ? '保存中...' : '保存配置' }}
        </button>
        <div class="flex gap-2">
          <button
            type="button"
            @click="runSyncAll"
            :disabled="!config.enabled || syncing || !canPreview || pendingCount === 0"
            class="px-4 py-2 bg-indigo-100 text-indigo-700 rounded-lg text-sm font-medium hover:bg-indigo-200 disabled:opacity-50"
          >
            同步全部待同步 ({{ pendingCount }})
          </button>
          <button
            type="button"
            @click="runSyncSelected"
            :disabled="!config.enabled || syncing || selectedIds.length === 0"
            class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-2"
          >
            <span v-if="syncing" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
            同步选中 ({{ selectedIds.length }})
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import Switch from './Switch.vue'
import { useToast } from '../composables/useToast'

interface FieldMap {
  id: string
  user_name: string
  real_name: string | null
  remark: string | null
}

interface SyncConfig {
  enabled: boolean
  connection_config_id: number | null
  table_name: string | null
  field_map: FieldMap
  schedule: 'off' | 'hourly' | 'daily' | 'weekly'
}

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ close: []; synced: [] }>()
const { showToast } = useToast()

const scheduleOptions = [
  { value: 'off' as const, label: '关闭', desc: '不执行定时同步' },
  { value: 'hourly' as const, label: '每小时', desc: '每小时整点执行' },
  { value: 'daily' as const, label: '每天', desc: '每天凌晨 2:00' },
  { value: 'weekly' as const, label: '每周', desc: '每周一凌晨 2:00' },
]

const mappingFields = [
  { key: 'id' as const, label: '用户 ID', required: true },
  { key: 'user_name' as const, label: '用户名', required: true },
  { key: 'real_name' as const, label: '真实姓名', required: false },
  { key: 'remark' as const, label: '备注', required: false },
]

const saving = ref(false)
const syncing = ref(false)
const loadingTables = ref(false)
const loadingColumns = ref(false)
const loadingPreview = ref(false)
const hideSynced = ref(true)
const selectedIds = ref<number[]>([])

const datasources = ref<any[]>([])
const tables = ref<any[]>([])
const columns = ref<any[]>([])
const previewUsers = ref<any[]>([])

const defaultConfig = (): SyncConfig => ({
  enabled: false,
  connection_config_id: null,
  table_name: null,
  field_map: { id: '', user_name: '', real_name: null, remark: null },
  schedule: 'off',
})

const config = ref<SyncConfig>(defaultConfig())

const canPreview = computed(
  () =>
    config.value.enabled &&
    !!config.value.connection_config_id &&
    !!config.value.table_name &&
    !!config.value.field_map.id &&
    !!config.value.field_map.user_name
)

const filteredPreview = computed(() => {
  let list = previewUsers.value
  if (hideSynced.value) list = list.filter((u) => !u.is_synced)
  return list
})

const pendingCount = computed(() => previewUsers.value.filter((u) => !u.is_synced).length)

const isAllSelected = computed(() => {
  const syncable = filteredPreview.value.filter((u) => !u.is_synced)
  return syncable.length > 0 && syncable.every((u) => selectedIds.value.includes(u.id))
})

const loadConfig = async () => {
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/config')
    const data = res.data?.data ?? res.data
    config.value = {
      ...defaultConfig(),
      ...data,
      field_map: { ...defaultConfig().field_map, ...(data?.field_map || {}) },
    }
  } catch {
    showToast('加载同步配置失败', 'error')
  }
}

const loadDatasources = async () => {
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/datasources')
    datasources.value = res.data?.items ?? []
  } catch {
    showToast('加载数据源列表失败', 'error')
  }
}

const loadTables = async () => {
  if (!config.value.connection_config_id) return
  loadingTables.value = true
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/tables', {
      params: { connection_config_id: config.value.connection_config_id },
    })
    tables.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载表列表失败', 'error')
  } finally {
    loadingTables.value = false
  }
}

const loadColumns = async () => {
  if (!config.value.connection_config_id || !config.value.table_name) return
  loadingColumns.value = true
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/columns', {
      params: {
        connection_config_id: config.value.connection_config_id,
        table_name: config.value.table_name,
      },
    })
    columns.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载字段列表失败', 'error')
  } finally {
    loadingColumns.value = false
  }
}

const onDatasourceChange = () => {
  config.value.table_name = null
  config.value.field_map = { id: '', user_name: '', real_name: null, remark: null }
  tables.value = []
  columns.value = []
  previewUsers.value = []
  loadTables()
}

const onTableChange = () => {
  config.value.field_map = { id: '', user_name: '', real_name: null, remark: null }
  previewUsers.value = []
  loadColumns()
}

const saveConfig = async () => {
  if (config.value.enabled) {
    if (!config.value.field_map.id || !config.value.field_map.user_name) {
      showToast('用户 ID 和用户名字段映射为必填项', 'error')
      return
    }
  }
  saving.value = true
  try {
    const res = await axios.put('/api/portal/management/third-party-sync/config', config.value)
    config.value = { ...config.value, ...(res.data?.data || {}) }
    showToast('配置已保存', 'success')
    if (canPreview.value) await loadPreview()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '保存配置失败', 'error')
  } finally {
    saving.value = false
  }
}

const loadPreview = async () => {
  if (!canPreview.value) return
  loadingPreview.value = true
  selectedIds.value = []
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/preview')
    previewUsers.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载预览失败', 'error')
  } finally {
    loadingPreview.value = false
  }
}

const toggleSelect = (id: number) => {
  const idx = selectedIds.value.indexOf(id)
  if (idx >= 0) selectedIds.value.splice(idx, 1)
  else selectedIds.value.push(id)
}

const toggleSelectAll = () => {
  const syncable = filteredPreview.value.filter((u) => !u.is_synced)
  if (isAllSelected.value) selectedIds.value = []
  else selectedIds.value = syncable.map((u) => u.id)
}

const runSync = async (userIds: number[] | null) => {
  syncing.value = true
  try {
    const res = await axios.post('/api/portal/management/third-party-sync/run', {
      user_ids: userIds,
    })
    showToast(res.data?.message || '同步完成', 'success')
    await loadPreview()
    emit('synced')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '同步失败', 'error')
  } finally {
    syncing.value = false
  }
}

const runSyncSelected = () => runSync(selectedIds.value)
const runSyncAll = () => runSync(null)

watch(
  () => props.visible,
  async (v) => {
    if (v) {
      previewUsers.value = []
      await Promise.all([loadConfig(), loadDatasources()])
      if (config.value.connection_config_id) await loadTables()
      if (config.value.table_name) await loadColumns()
      if (canPreview.value) await loadPreview()
    }
  }
)

onMounted(() => {
  if (props.visible) loadConfig()
})
</script>

<style scoped>
@keyframes slide-in-right {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
.animate-slide-in-right {
  animation: slide-in-right 0.25s ease-out;
}
</style>
