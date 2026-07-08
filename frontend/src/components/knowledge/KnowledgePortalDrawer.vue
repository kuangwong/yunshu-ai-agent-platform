<template>
  <teleport to="body">
    <div
      v-show="modelValue"
      :class="[
        'z-[120]',
        pinned && isMobile
          ? 'fixed inset-x-0 bottom-0 max-w-full flex flex-col justify-end pointer-events-none'
          : pinned
            ? 'fixed inset-y-0 right-0 max-w-full flex pointer-events-none'
            : isMobile
              ? 'fixed inset-0 flex flex-col overflow-hidden'
              : 'fixed inset-0 overflow-hidden',
      ]"
    >
      <transition
        v-if="!pinned"
        enter-active-class="ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-show="modelValue"
          :class="[
            'bg-gray-500/30 backdrop-blur-xs transition-opacity',
            isMobile ? 'flex-1 min-h-0 w-full' : 'absolute inset-0',
          ]"
          @click="closeDrawer"
        />
      </transition>

      <div
        :class="[
          pinned
            ? isMobile
              ? 'w-full flex pointer-events-auto min-h-0 max-h-[58%]'
              : 'h-full flex pointer-events-auto'
            : isMobile
              ? 'w-full flex justify-center min-h-0 max-h-[92%] shrink-0'
              : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
        ]"
      >
        <transition
          enter-active-class="transform transition ease-in-out duration-300"
          :enter-from-class="sheetEnterFrom"
          enter-to-class="translate-x-0 translate-y-0"
          leave-active-class="transform transition ease-in-out duration-300"
          leave-from-class="translate-x-0 translate-y-0"
          :leave-to-class="sheetLeaveTo"
        >
          <div
            v-show="modelValue"
            :class="[
              'bg-white dark:bg-gray-900 shadow-2xl flex flex-col relative z-10 min-h-0 pb-[env(safe-area-inset-bottom,0px)]',
              isMobile
                ? 'w-full max-w-none rounded-t-2xl border-t border-gray-200 dark:border-gray-800 h-full max-h-full'
                : 'w-screen max-w-[min(100vw,28rem)] h-full border-l border-gray-200 dark:border-gray-800',
            ]"
          >
            <!-- Drawer pull handle for mobile -->
            <div
              v-if="isMobile"
              class="shrink-0 flex justify-center pt-2 pb-1"
              aria-hidden="true"
            >
              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
            </div>

            <!-- Header -->
            <div
              class="shrink-0 px-4 py-3 sm:py-4 border-b border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 flex items-center justify-between gap-2"
            >
              <span class="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5 select-none min-w-0">
                <svg class="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
                <span class="truncate">知识库中心</span>
                <span
                  v-if="pinned"
                  class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide text-green-600 bg-green-50 border border-green-100 dark:text-green-300 dark:bg-green-500/10 dark:border-green-500/20"
                >
                  已钉住
                </span>
              </span>
              <div class="flex items-center gap-2 flex-shrink-0">
                <label
                  class="hidden sm:flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400 cursor-pointer select-none whitespace-nowrap"
                  title="开启后点击推荐问题不会关闭抽屉，可连续提问"
                >
                  <input
                    v-model="keepOpenOnQuestion"
                    type="checkbox"
                    class="rounded border-gray-300 text-primary focus:ring-primary/30"
                  />
                  提问后保持
                </label>
                <button
                  type="button"
                  class="hidden sm:inline-flex text-gray-400 hover:text-green-600 dark:hover:text-green-400 p-1 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  :class="{ 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-500/10': pinned }"
                  :title="pinned ? '取消钉住' : '钉住侧栏'"
                  @click="pinned = !pinned"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 17v5M9 10.76a2 2 0 01-1.11 1.79l-1.78.9A2 2 0 005 15.24V16a1 1 0 001 1h12a1 1 0 001-1v-.76a2 2 0 00-1.11-1.79l-1.78-.9A2 2 0 0115 10.76V7a1 1 0 00-1-1h-4a1 1 0 00-1 1v3.76" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 p-1.5 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  title="关闭 (Esc)"
                  @click="closeDrawer"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

             <!-- Content List -->
            <div class="flex-1 overflow-y-auto p-4 bg-gray-50/50 dark:bg-gray-900/40 min-h-0 space-y-3 scrollbar-thin">
              <!-- Overview Card -->
              <div class="bg-white dark:bg-gray-800/80 backdrop-blur-xs border border-gray-150 dark:border-gray-800 rounded-xl p-3 flex flex-row items-center justify-between gap-2 shadow-xs select-none">
                <div class="flex items-center gap-2.5 min-w-0">
                  <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-green-600 dark:bg-green-500 text-white shadow-sm flex-shrink-0 text-xs font-bold">
                    📚
                  </div>
                  <div class="min-w-0">
                    <h3 class="text-xs font-bold text-gray-900 dark:text-gray-100 tracking-wide">我的知识库中心</h3>
                    <div class="flex flex-wrap items-center gap-1.5 mt-0.5 text-[9px]">
                      <span class="font-semibold text-green-600 dark:text-green-400">
                        {{ datasets.length }} 个知识库
                      </span>
                      <template v-if="generatedAt">
                        <span class="text-gray-300 dark:text-gray-700">|</span>
                        <span class="text-gray-500 dark:text-gray-400">更新 {{ generatedAt }}</span>
                      </template>
                    </div>
                  </div>
                </div>
                
                <button
                  type="button"
                  class="w-7 h-7 flex items-center justify-center rounded-lg border border-transparent bg-gray-50 text-gray-500 hover:bg-gray-100 hover:text-gray-750 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-750 dark:hover:text-gray-200 transition-all cursor-pointer active:scale-90 flex-shrink-0"
                  title="刷新知识库列表"
                  :disabled="loading"
                  @click="emit('refresh')"
                >
                  <svg 
                    class="w-3.5 h-3.5"
                    :class="{ 'animate-spin': loading }"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                  </svg>
                </button>
              </div>

              <!-- ⚙️ 高级选项 (Collapsible) -->
              <div class="rounded-xl border border-blue-150/70 dark:border-blue-900/40 bg-blue-50/10 dark:bg-blue-950/5 p-3 space-y-2.5">
                <div
                  class="flex items-center justify-between w-full text-[10px] font-bold text-blue-700 dark:text-blue-400 uppercase tracking-wider transition-colors select-none cursor-pointer"
                  @click="showAdvancedConfig = !showAdvancedConfig"
                >
                  <span class="flex items-center gap-1.5">
                    <span>🛠️</span> 高级配置
                  </span>
                  <svg
                    class="w-3.5 h-3.5 transform transition-transform duration-300 pointer-events-none"
                    :class="{ 'rotate-180': showAdvancedConfig }"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>

                <transition
                  enter-active-class="transition-all duration-300 ease-out"
                  enter-from-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
                  enter-to-class="transform opacity-100 translate-y-0 max-h-[380px] overflow-hidden"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="transform opacity-100 translate-y-0 max-h-[380px] overflow-hidden"
                  leave-to-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
                >
                  <div v-show="showAdvancedConfig" class="space-y-3 pt-1 select-none">
                    <!-- 反幻觉检测胶囊开关 -->
                    <div class="flex items-center justify-between bg-white dark:bg-gray-800 p-2.5 rounded-lg border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex flex-col min-w-0">
                        <span class="text-[11px] font-bold text-gray-850 dark:text-gray-200 flex items-center gap-1.5">
                          启用反幻觉检测
                          <button type="button" @click.stop="toggleTooltip('hallucination')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="text-[9px] text-gray-400 dark:text-gray-500 mt-0.5">
                          二次核查回答与文献一致性
                        </span>
                      </div>
                      
                      <button
                        type="button"
                        class="relative inline-flex h-4.5 w-8 shrink-0 cursor-pointer rounded-full border border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
                        :class="[hallucinationCheck ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700']"
                        @click="hallucinationCheck = !hallucinationCheck"
                      >
                        <span
                          class="pointer-events-none inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                          :class="[hallucinationCheck ? 'translate-x-3.5' : 'translate-x-0']"
                        />
                      </button>
                    </div>

                    <transition
                      enter-active-class="transition-all duration-200 ease-out"
                      enter-from-class="opacity-0 max-h-0"
                      enter-to-class="opacity-100 max-h-[80px]"
                      leave-active-class="transition-all duration-150 ease-in"
                      leave-from-class="opacity-100 max-h-[80px]"
                      leave-to-class="opacity-0 max-h-0"
                    >
                      <div v-if="activeTooltip === 'hallucination'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                        开启后，系统将使用反幻觉大模型二次审视生成的回答是否完全忠实于事实文献，如存在偏差将自动重写。关闭可显著提升问答的响应速度。
                      </div>
                    </transition>

                    <!-- Similarity Threshold -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          相似度阈值
                          <button type="button" @click.stop="toggleTooltip('threshold')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ similarityThreshold }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'threshold'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          常规知识库检索时的相似度阈值（0.0 至 1.0）。低于此设定值的检索结果将被过滤，以防混入无关文档，推荐配置为 0.20。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="range"
                          v-model.number="similarityThreshold"
                          min="0.0"
                          max="1.0"
                          step="0.05"
                          class="flex-1 accent-green-600 h-1 bg-gray-200 dark:bg-gray-750 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div class="flex items-center justify-between text-[8px] text-gray-450 dark:text-gray-500 font-mono select-none">
                        <span>0.0 (无门槛)</span>
                        <span>0.5</span>
                        <span>1.0 (极严格)</span>
                      </div>
                    </div>

                    <!-- Vector Weight -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          语义权重占比
                          <button type="button" @click.stop="toggleTooltip('weight')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ vectorWeight }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'weight'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          常规知识库检索时向量相似度权重的占比（0.0 至 1.0），其余权重为全文关键词匹配。推荐配置为 0.30。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="range"
                          v-model.number="vectorWeight"
                          min="0.0"
                          max="1.0"
                          step="0.05"
                          class="flex-1 accent-green-600 h-1 bg-gray-200 dark:bg-gray-750 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div class="flex items-center justify-between text-[8px] text-gray-450 dark:text-gray-500 font-mono select-none">
                        <span>0.0 (纯关键词)</span>
                        <span>0.5</span>
                        <span>1.0 (纯向量)</span>
                      </div>
                    </div>

                    <!-- Metadata Top_K -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          最大召回分块数
                          <button type="button" @click.stop="toggleTooltip('top_k')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ metadataTopK }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'top_k'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          最大检索召回的文档切片数量。数量越多，AI 可参考的事实越丰富，但也会增加生成时的上下文 Token 消耗。推荐范围：5 ~ 10。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="number"
                          v-model.number="metadataTopK"
                          min="1"
                          max="50"
                          class="w-full px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-850 dark:text-gray-100 focus:outline-none focus:border-green-500 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                </transition>
              </div>

              <!-- Loading State -->
              <div v-if="loading" class="space-y-3 py-6">
                <div v-for="i in 3" :key="i" class="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-100 dark:border-gray-800 animate-pulse space-y-3">
                  <div class="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/3"></div>
                  <div class="h-3 bg-gray-200 dark:bg-gray-700 rounded w-2/3"></div>
                </div>
              </div>

              <!-- Empty State -->
              <div v-else-if="datasets.length === 0" class="flex flex-col items-center justify-center py-20 text-gray-400 dark:text-gray-500 text-center px-6">
                <svg class="w-12 h-12 text-gray-300 dark:text-gray-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 15v2m0 0v2m0-2h2m-2 0H8m13 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <h4 class="text-xs font-bold text-gray-700 dark:text-gray-300">暂无可用的知识库</h4>
                <p class="text-[11px] text-gray-400 dark:text-gray-500 mt-2 max-w-[18rem] leading-relaxed">
                  您当前可能没有已被分配的知识库权限。系统级默认公开的知识库也未配置，请联系管理员为您进行角色与知识授权配置。
                </p>
              </div>

              <!-- Datasets Cards -->
              <div
                v-else
                v-for="ds in datasets"
                :key="ds.id"
                class="bg-white dark:bg-gray-800 rounded-xl border transition-all duration-200"
                :class="[
                  activeDatasetIds.includes(ds.id)
                    ? 'border-green-200 dark:border-green-900 shadow-md shadow-green-500/5 bg-green-50/10'
                    : 'border-gray-150 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'
                ]"
              >
                <!-- Card Header Info -->
                <div class="p-4 flex items-start gap-3 select-none">
                  <div 
                    class="p-2 rounded-lg text-xs font-bold flex-shrink-0 transition-colors"
                    :class="[
                      activeDatasetIds.includes(ds.id)
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400'
                        : 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400'
                    ]"
                  >
                    📚
                  </div>
                  
                  <div class="min-w-0 flex-1 cursor-pointer" @click="toggleExpand(ds.id)">
                    <div class="flex items-center gap-1.5">
                      <h4 class="text-xs font-bold text-gray-800 dark:text-gray-100 truncate">
                        {{ ds.name }}
                      </h4>
                      <svg
                        class="w-3.5 h-3.5 text-gray-400 transition-transform duration-200 flex-shrink-0"
                        :class="{ 'rotate-180': expandedId === ds.id }"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                      </svg>
                    </div>
                    <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-1 line-clamp-1">
                      {{ ds.description || '暂无描述信息' }}
                    </p>
                    <!-- Badges -->
                    <div class="flex items-center gap-2 mt-2">
                      <span class="inline-flex items-center text-[9px] text-gray-400 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded-md select-none">
                        📄 {{ ds.document_count ?? ds.doc_count ?? 0 }} 个文件
                      </span>
                      <span v-if="activeDatasetIds.includes(ds.id)" class="inline-flex items-center text-[9px] text-green-600 bg-green-50 dark:bg-green-900/20 dark:text-green-400 px-1.5 py-0.5 rounded-md font-bold select-none">
                        ● 已启用
                      </span>
                    </div>
                  </div>

                  <!-- Toggle switch -->
                  <button
                    type="button"
                    class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-1 focus:ring-green-500/20"
                    :class="[activeDatasetIds.includes(ds.id) ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700']"
                    @click="emit('toggle-active', ds.id)"
                  >
                    <span
                      class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                      :class="[activeDatasetIds.includes(ds.id) ? 'translate-x-4' : 'translate-x-0']"
                    />
                  </button>
                </div>

                <!-- Recommendation accordion area -->
                <transition
                  enter-active-class="transition-all duration-200 ease-out"
                  enter-from-class="max-h-0 opacity-0"
                  enter-to-class="max-h-[300px] opacity-100"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="max-h-[300px] opacity-100"
                  leave-to-class="max-h-0 opacity-0"
                >
                  <div
                    v-show="expandedId === ds.id"
                    class="border-t border-gray-100 dark:border-gray-700/60 bg-gray-50/30 dark:bg-gray-800/20 overflow-hidden"
                  >
                    <div class="p-3.5 space-y-2">
                      <div class="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1 flex items-center gap-1.5 select-none">
                        <svg class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                        知识库建议提问
                      </div>

                      <!-- recommendations loading spinner -->
                      <div v-if="recommendations[ds.id]?.loading" class="flex justify-center py-4">
                        <div class="animate-spin rounded-full h-4 w-4 border-2 border-gray-300 border-t-primary" />
                      </div>

                      <div v-else-if="recommendations[ds.id]?.questions?.length > 0" class="flex flex-col gap-1.5">
                        <button
                          v-for="(q, idx) in recommendations[ds.id].questions"
                          :key="idx"
                          @click="handleQuestionClick(q.query)"
                          class="w-full text-left p-2 rounded-lg text-xs text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-750 hover:bg-green-50/20 hover:border-green-200 dark:hover:bg-green-900/10 dark:hover:border-green-950 transition-all text-ellipsis overflow-hidden break-all leading-normal"
                        >
                          ❓ {{ q.query }}
                        </button>
                      </div>

                      <div v-else class="text-[11px] text-gray-400 text-center py-2 select-none">
                        该库下暂无建议提问，请先上传文档喔
                      </div>
                    </div>
                  </div>
                </transition>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";

const modelValue = defineModel<boolean>({ default: false });
const keepOpenOnQuestion = defineModel<boolean>("keepOpenOnQuestion", { default: false });
const pinned = defineModel<boolean>("pinned", { default: false });
const hallucinationCheck = defineModel<boolean>("hallucinationCheck", { default: false });

const similarityThreshold = defineModel<number>("similarityThreshold", { default: 0.20 });
const vectorWeight = defineModel<number>("vectorWeight", { default: 0.30 });
const metadataTopK = defineModel<number>("metadataTopK", { default: 5 });

const props = defineProps<{
  datasets: Array<{
    id: string;
    name: string;
    description: string;
    doc_count?: number;
    word_count?: number;
    [key: string]: any;
  }>;
  activeDatasetIds: string[];
  recommendations: Record<string, { questions: any[]; loading?: boolean }>;
  loading?: boolean;
  generatedAt?: string;
}>();

const emit = defineEmits<{
  (e: "toggle-active", id: string): void;
  (e: "quick-question", query: string, action?: "send" | "fill"): void;
  (e: "load-recommendations", id: string): void;
  (e: "refresh"): void;
}>();

const expandedId = ref<string | null>(null);
const showAdvancedConfig = ref(false);
const activeTooltip = ref<string | null>(null);

const isMobile = computed(() => {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 639px)").matches;
});

const sheetEnterFrom = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));
const sheetLeaveTo = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));

const toggleExpand = (id: string) => {
  if (expandedId.value === id) {
    expandedId.value = null;
  } else {
    expandedId.value = id;
    emit("load-recommendations", id);
  }
};

const toggleTooltip = (paramName: string) => {
  if (activeTooltip.value === paramName) {
    activeTooltip.value = null;
  } else {
    activeTooltip.value = paramName;
  }
};

const closeDrawer = () => {
  modelValue.value = false;
};

const handleQuestionClick = (query: string) => {
  emit("quick-question", query, "send");
  if (!keepOpenOnQuestion.value) {
    closeDrawer();
  }
};
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar {
  width: 4px;
}
.scrollbar-thin::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 9999px;
}
.scrollbar-thin::-webkit-scrollbar-track {
  background-color: transparent;
}
</style>
