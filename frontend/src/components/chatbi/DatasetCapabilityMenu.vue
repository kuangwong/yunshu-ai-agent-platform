<template>
  <section ref="menuContainer" class="space-y-4">
    <!-- Header -->
    <div class="bg-gray-50/40 dark:bg-gray-800/10 backdrop-blur-sm border border-gray-150 dark:border-gray-800/80 rounded-xl p-3 flex flex-col sm:flex-row justify-between sm:items-center gap-3">
      <div class="flex items-center gap-2.5">
        <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-600 dark:bg-blue-500 text-white shadow-sm flex-shrink-0">
          <svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z"/>
          </svg>
        </div>
        <div class="min-w-0">
          <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100 tracking-wide">我的数据门户</h3>
          <div class="flex flex-wrap items-center gap-1.5 mt-0.5 text-[10px]">
            <span
              v-if="payload.dataset_count !== undefined"
              class="font-semibold text-blue-600 dark:text-blue-400"
            >
              {{ payload.dataset_count }} 个数据集
            </span>
            <span class="text-gray-300 dark:text-gray-700">|</span>
            <span v-if="payload.generated_at" class="text-gray-500 dark:text-gray-400">
              更新时间 {{ formattedGeneratedAt }}
            </span>
            <template v-if="payload.dataset_menu_hash">
              <span class="text-gray-300 dark:text-gray-700">|</span>
              <span class="text-gray-400 dark:text-gray-500 font-mono">
                {{ payload.dataset_menu_hash.slice(0, 8) }}
              </span>
            </template>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg border border-transparent bg-gray-100 hover:bg-gray-200/70 dark:bg-gray-800 dark:hover:bg-gray-750 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-all active:scale-90 cursor-pointer"
        title="刷新数据门户"
        @click="handleRefreshClick"
      >
        <svg 
          class="w-4 h-4 transition-transform duration-700"
          :class="{ 'animate-spin': isRefreshing }"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
      </button>
    </div>

    <!-- Search and Filter Bar -->
    <div class="space-y-2.5">
      <!-- Search Input -->
      <div class="relative">
        <span class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none text-gray-400 dark:text-gray-500">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </span>
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索场景、表名或指标..."
          class="w-full pl-9 pr-8 py-1.5 text-xs rounded-xl border border-gray-150 dark:border-gray-800 bg-white dark:bg-gray-900/30 text-gray-850 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all shadow-xs"
        />
        <button
          v-if="searchQuery"
          type="button"
          class="absolute inset-y-0 right-0 flex items-center pr-2.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
          @click="searchQuery = ''"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Scrollable Tags Filter -->
      <div v-if="allTags.length" class="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1 -mx-0.5">
        <button
          type="button"
          class="px-2.5 py-1 text-[10px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95"
          :class="selectedTag === 'All'
            ? 'bg-blue-600 border-transparent text-white shadow-xs'
            : 'bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
          @click="selectedTag = 'All'"
        >
          全部
        </button>
        <button
          v-for="tag in allTags"
          :key="tag"
          type="button"
          class="px-2.5 py-1 text-[10px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95"
          :class="selectedTag === tag
            ? 'bg-blue-600 border-transparent text-white shadow-xs'
            : 'bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
          @click="selectedTag = tag"
        >
          {{ tag }}
        </button>
      </div>
    </div>

    <!-- Cards Grid Container with Loading Overlay -->
    <div class="relative">
      <!-- Loading Overlay -->
      <transition
        enter-active-class="transition-opacity duration-200"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="transition-opacity duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div 
          v-if="isRefreshing" 
          class="absolute inset-0 bg-white/60 dark:bg-gray-900/60 backdrop-blur-[2px] z-20 rounded-xl flex items-start justify-center pt-24 pointer-events-auto"
        >
          <div class="flex flex-col items-center gap-2">
            <svg class="w-8 h-8 animate-spin text-blue-600 dark:text-blue-500" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-xs font-bold text-gray-500 dark:text-gray-400 select-none animate-pulse">正在刷新数据门户...</span>
          </div>
        </div>
      </transition>

      <div class="grid gap-4" :class="{ 'pointer-events-none select-none': isRefreshing }">
        <article
          v-for="(group, idx) in filteredGroups"
          :key="group.id || group.title"
          class="group/card rounded-xl border border-gray-150 dark:border-gray-800/80 bg-white dark:bg-gray-900/30 p-3.5 sm:p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-300"
        >
          <!-- Card Title -->
          <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-2.5">
            <div class="flex items-start gap-2.5 min-w-0">
              <!-- Icon -->
              <div 
                class="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-lg shadow-sm border"
                :class="[
                  getGroupVisuals(idx, group.title).bg,
                  getGroupVisuals(idx, group.title).text,
                  getGroupVisuals(idx, group.title).border
                ]"
                v-html="getGroupVisuals(idx, group.title).icon"
              ></div>
              <div class="min-w-0">
                <h4 class="text-sm font-bold text-gray-900 dark:text-gray-100 leading-normal flex items-center gap-1.5">
                  {{ group.title }}
                </h4>
                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-normal">
                  {{ group.summary }}
                </p>
              </div>
            </div>
            
            <!-- Tags -->
            <div v-if="group.tags?.length" class="flex flex-wrap gap-1 mt-1 sm:mt-0 sm:justify-end flex-shrink-0 ml-10.5 sm:ml-0">
              <span
                v-for="tag in group.tags.slice(0, 3)"
                :key="tag"
                class="rounded-full bg-gray-50 dark:bg-gray-800/80 border border-gray-100 dark:border-gray-700/60 px-2 py-0.5 text-[9px] font-bold text-gray-500 dark:text-gray-400"
              >
                {{ tag }}
              </span>
            </div>
          </div>

          <!-- You Can Ask Section -->
          <div v-if="group.questions?.length" class="mt-4">
            <div class="mb-2 text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-1 select-none">
              <svg class="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
              你可以这样问
            </div>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="question in group.questions"
                :key="question.query"
                type="button"
                class="group/btn relative inline-flex items-center gap-1.5 rounded-lg border border-blue-100 dark:border-blue-900/30 bg-blue-50/30 dark:bg-blue-950/20 px-3 py-2 text-left text-xs font-semibold text-blue-700 dark:text-blue-300 transition-all hover:bg-blue-50 hover:border-blue-300/60 dark:hover:bg-blue-900/40 hover:-translate-y-0.5 active:translate-y-0 shadow-sm"
                @click="handleQuestionClick(question, group)"
              >
                <svg class="w-3.5 h-3.5 text-blue-400/80 group-hover/btn:text-blue-500 dark:text-blue-400/60 dark:group-hover/btn:text-blue-300 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                </svg>
                <span>{{ question.label }}</span>
                <span 
                  v-if="question.click_count" 
                  class="ml-1 inline-flex items-center px-1 py-0.5 rounded bg-amber-500/10 text-[9px] font-bold text-amber-600 border border-amber-500/20 dark:bg-amber-500/20 dark:text-amber-300 dark:border-amber-500/30 shadow-sm"
                >
                  🔥 常用 {{ question.click_count }}
                </span>
              </button>
            </div>
          </div>

          <!-- Related Data Section -->
          <div v-if="group.related_data?.length" class="mt-4 border-t border-gray-100 dark:border-gray-800/80 pt-3">
            <button
              type="button"
              class="flex items-center justify-between w-full text-left text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider hover:text-gray-600 dark:hover:text-gray-300 transition-colors select-none"
              @click="toggleGroup(group.id || group.title)"
            >
              <span class="flex items-center gap-1.5">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
                </svg>
                相关数据
              </span>
              <svg
                class="w-3.5 h-3.5 transform transition-transform duration-300"
                :class="{ 'rotate-180': expandedGroups[group.id || group.title] }"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            
            <div 
              class="grid transition-all duration-300 ease-in-out"
              :class="expandedGroups[group.id || group.title] ? 'grid-rows-[1fr] opacity-100 mt-2.5 overflow-visible' : 'grid-rows-[0fr] opacity-0 overflow-hidden'"
            >
              <div class="min-h-0 overflow-visible">
                <div class="space-y-3 bg-gray-50/50 dark:bg-gray-950/20 rounded-xl p-3 border border-gray-100 dark:border-gray-800">
                  <div v-for="related in group.related_data" :key="related.dataset || related.display_name" class="space-y-1.5">
                    <div class="text-[11px] font-bold text-gray-500 dark:text-gray-400 flex items-center gap-1 select-none">
                      <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2z"/>
                      </svg>
                      {{ related.display_name || related.dataset }}
                    </div>
                    <div class="flex flex-wrap gap-1.5 overflow-visible">
                      <div
                        v-for="table in related.tables || []"
                        :key="table"
                        class="relative inline-block overflow-visible"
                        @mouseenter="handleMouseEnter($event, `${group.id || group.title}_${related.dataset || related.display_name || ''}_${table}`)"
                        @mouseleave="handleMouseLeave"
                      >
                        <span
                          class="inline-flex items-center gap-1 rounded bg-white dark:bg-gray-800 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:text-gray-300 ring-1 ring-gray-100 dark:ring-gray-700/60 shadow-sm hover:scale-102 hover:shadow-xs transition-all duration-200 cursor-pointer select-none active:scale-95"
                          @click.stop="handleClick($event, `${group.id || group.title}_${related.dataset || related.display_name || ''}_${table}`)"
                        >
                          <svg class="w-2.5 h-2.5 text-gray-400 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                          </svg>
                          {{ table }}
                        </span>

                        <!-- Popover Data Dictionary -->
                        <transition
                          enter-active-class="transition-all duration-200 ease-out"
                          enter-from-class="opacity-0 translate-y-1 scale-95"
                          enter-to-class="opacity-100 translate-y-0 scale-100"
                          leave-active-class="transition-all duration-150 ease-in"
                          leave-from-class="opacity-100 translate-y-0 scale-100"
                          leave-to-class="opacity-0 translate-y-1 scale-95"
                        >
                          <div 
                            v-if="(activeTableDictionary === `${group.id || group.title}_${related.dataset || related.display_name || ''}_${table}` || pinnedTableDictionary === `${group.id || group.title}_${related.dataset || related.display_name || ''}_${table}`) && related.table_columns?.[table]?.length" 
                            class="absolute z-45 bottom-full mb-2 w-72 bg-white dark:bg-gray-850 border border-gray-150 dark:border-gray-750 rounded-xl p-3 shadow-xl text-left select-none ring-1 ring-black/5"
                            :style="popoverStyles[`${group.id || group.title}_${related.dataset || related.display_name || ''}_${table}`] || { left: '50%', transform: 'translateX(-50%)' }"
                            @click.stop
                          >
                            <h5 class="text-[10px] font-bold text-gray-800 dark:text-gray-250 mb-2 border-b border-gray-100 dark:border-gray-750 pb-1 flex items-center justify-between">
                              <span class="flex items-center gap-1 truncate">
                                <svg class="w-3.5 h-3.5 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
                                </svg>
                                <span class="truncate">{{ table }} 字段字典</span>
                              </span>
                              <button 
                                type="button" 
                                class="text-gray-400 hover:text-gray-650 dark:hover:text-gray-200 w-4 h-4 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors flex-shrink-0 cursor-pointer"
                                @click.stop="pinnedTableDictionary = null; activeTableDictionary = null"
                              >
                                &times;
                              </button>
                            </h5>
                            <div class="max-h-48 overflow-y-auto no-scrollbar space-y-2 pr-0.5">
                              <div 
                                v-for="col in related.table_columns[table]" 
                                :key="col.name" 
                                class="flex flex-col border-b border-gray-50/50 dark:border-gray-750/30 pb-1.5 last:border-0 last:pb-0"
                              >
                                <div class="flex items-center justify-between gap-2">
                                  <span class="font-mono text-[9px] font-bold text-blue-600 dark:text-blue-400 truncate">{{ col.name }}</span>
                                  <span class="text-[8px] bg-gray-100 dark:bg-gray-700/80 px-1 rounded text-gray-500 dark:text-gray-400 font-bold uppercase">{{ col.type }}</span>
                                </div>
                                <div class="text-[9px] text-gray-500 dark:text-gray-450 mt-0.5 font-medium leading-normal">
                                  {{ col.term }}
                                  <span v-if="col.description && col.description !== col.term" class="text-gray-400 dark:text-gray-500 ml-1 text-[8.5px] font-normal">
                                    ({{ col.description }})
                                  </span>
                                </div>
                              </div>
                            </div>
                            <!-- 快捷提问按钮组 -->
                            <div class="mt-2.5 pt-2.5 border-t border-gray-100 dark:border-gray-750 flex items-center justify-between gap-1">
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-blue-50 hover:bg-blue-100 dark:bg-blue-950/30 dark:hover:bg-blue-900/40 text-blue-600 dark:text-blue-400 border border-blue-100/50 dark:border-blue-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap"
                                title="分析表结构和字段口径"
                                @click.stop="handleQuickQuestionClick('structure', table, related)"
                              >
                                <span>📖 结构说明</span>
                              </button>
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/30 dark:hover:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400 border border-emerald-100/50 dark:border-emerald-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap"
                                title="查询最近100条明细数据"
                                @click.stop="handleQuickQuestionClick('query', table, related)"
                              >
                                <span>📊 查询明细</span>
                              </button>
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-amber-50 hover:bg-amber-100 dark:bg-amber-950/30 dark:hover:bg-amber-900/40 text-amber-600 dark:text-amber-400 border border-amber-100/50 dark:border-amber-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap"
                                title="推荐业务分析问题"
                                @click.stop="handleQuickQuestionClick('recommend', table, related)"
                              >
                                <span>💡 推荐提问</span>
                              </button>
                            </div>
                          </div>
                        </transition>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Follow-ups Section -->
          <div v-if="group.followups?.length" class="mt-4 border-t border-gray-100 dark:border-gray-800/80 pt-3">
            <div class="mb-2 text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-1 select-none">
              <svg class="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>
              </svg>
              继续追问
            </div>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="followup in group.followups"
                :key="followup.query"
                type="button"
                class="inline-flex items-center gap-1 px-2.5 py-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors bg-gray-50/50 dark:bg-gray-800/20 hover:bg-blue-50/30 dark:hover:bg-blue-900/20 border border-gray-200/50 dark:border-gray-700 hover:border-blue-100 dark:hover:border-blue-900/40 rounded-lg shadow-xs active:scale-95 font-medium"
                @click="emitQuickQuestion(followup.query)"
              >
                <span>{{ followup.label }}</span>
              </button>
            </div>
          </div>
        </article>
      </div>

      <!-- Empty State -->
      <div
        v-if="filteredGroups.length === 0"
        class="bg-gray-50/20 dark:bg-gray-900/10 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl p-8 flex flex-col items-center justify-center text-center space-y-2 select-none"
      >
        <div class="text-gray-300 dark:text-gray-600">
          <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <h4 class="text-xs font-bold text-gray-500 dark:text-gray-400">未找到相关数据场景</h4>
        <p class="text-[10px] text-gray-400 dark:text-gray-500 max-w-[200px]">尝试精简搜索词，或切换其他分类标签</p>
      </div>

    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from "vue";

interface DatasetCapabilityQuestion {
  label: string;
  query: string;
  type?: string;
  click_count?: number;
  last_clicked_at?: string;
}

interface DatasetColumnInfo {
  name: string;
  term: string;
  type: string;
  description: string;
}

interface DatasetCapabilityRelatedData {
  dataset?: string;
  display_name?: string;
  tables?: string[];
  table_descriptions?: Array<{ name: string; description?: string }>;
  table_columns?: Record<string, DatasetColumnInfo[]>;
  table_physical_names?: Record<string, string>;
}

interface DatasetCapabilityGroup {
  id?: string;
  title: string;
  summary: string;
  tags?: string[];
  questions?: DatasetCapabilityQuestion[];
  related_data?: DatasetCapabilityRelatedData[];
  followups?: DatasetCapabilityQuestion[];
}

interface DatasetNavigationPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: DatasetCapabilityGroup[];
  markdown?: string;
}

const props = defineProps<{
  payload: DatasetNavigationPayload;
}>();

const emit = defineEmits<{
  (event: "quick-question", query: string): void;
  (event: "record-question-click", payload: { query: string; label?: string; group_id?: string }): void;
  (event: "refresh"): void;
}>();

const menuContainer = ref<HTMLElement | null>(null);
const isRefreshing = ref(false);
const expandedGroups = ref<Record<string, boolean>>({});
const activeTableDictionary = ref<string | null>(null);
const pinnedTableDictionary = ref<string | null>(null);
const popoverStyles = ref<Record<string, Record<string, string>>>({});

const toggleTablePin = (uniqueId: string) => {
  if (pinnedTableDictionary.value === uniqueId) {
    pinnedTableDictionary.value = null;
  } else {
    pinnedTableDictionary.value = uniqueId;
  }
};

const handleGlobalClick = () => {
  pinnedTableDictionary.value = null;
};

const updatePopoverStyle = (badgeEl: HTMLElement, uniqueId: string) => {
  if (!badgeEl) {
    console.error("updatePopoverStyle fail: badgeEl is null");
    return;
  }
  if (!menuContainer.value) {
    console.error("updatePopoverStyle fail: menuContainer.value is null");
    return;
  }
  
  const badgeRect = badgeEl.getBoundingClientRect();
  const containerRect = menuContainer.value.getBoundingClientRect();
  
  const popoverWidth = 288; // w-72 is 288px
  const badgeCenterRelative = (badgeRect.left + badgeRect.width / 2) - containerRect.left;
  
  let left = badgeCenterRelative - popoverWidth / 2;
  const minLeft = 8;
  if (left < minLeft) {
    left = minLeft;
  }
  
  const maxLeft = containerRect.width - popoverWidth - 8;
  if (left > maxLeft) {
    left = maxLeft;
  }
  
  const badgeLeftRelative = badgeRect.left - containerRect.left;
  const popoverLeftOffset = left - badgeLeftRelative;
  
  console.error("updatePopoverStyle SUCCESS:", {
    uniqueId,
    badgeRectLeft: badgeRect.left,
    badgeWidth: badgeRect.width,
    containerRectLeft: containerRect.left,
    containerRectWidth: containerRect.width,
    badgeCenterRelative,
    left,
    badgeLeftRelative,
    popoverLeftOffset
  });
  
  popoverStyles.value[uniqueId] = {
    left: `${popoverLeftOffset}px`,
    transform: 'none',
  };
};

const handleMouseEnter = (event: MouseEvent, uniqueId: string) => {
  activeTableDictionary.value = uniqueId;
  const target = event.currentTarget as HTMLElement;
  if (target) {
    updatePopoverStyle(target, uniqueId);
  }
};

const handleMouseLeave = () => {
  activeTableDictionary.value = null;
};

const handleClick = (event: MouseEvent, uniqueId: string) => {
  toggleTablePin(uniqueId);
  const target = event.currentTarget as HTMLElement;
  if (target) {
    updatePopoverStyle(target, uniqueId);
  }
};

onMounted(() => {
  document.addEventListener("click", handleGlobalClick);
});

onUnmounted(() => {
  document.removeEventListener("click", handleGlobalClick);
});

const searchQuery = ref("");
const selectedTag = ref("All");

// 动态去重提取出当前有权访问的所有卡片 tags
const allTags = computed(() => {
  const tagsSet = new Set<string>();
  const groups = props.payload.groups || [];
  for (const group of groups) {
    if (group.tags) {
      for (const tag of group.tags) {
        const cleaned = String(tag || "").trim();
        if (cleaned) {
          tagsSet.add(cleaned);
        }
      }
    }
  }
  return Array.from(tagsSet);
});

// 计算属性：联合搜索与过滤
const filteredGroups = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  const tag = selectedTag.value;
  const groups = props.payload.groups || [];

  return groups.filter((group) => {
    // 1. Tag 过滤
    if (tag !== "All") {
      const groupTags = group.tags || [];
      if (!groupTags.some((t) => String(t).trim() === tag)) {
        return false;
      }
    }

    // 2. 搜索框过滤
    if (query) {
      const title = (group.title || "").toLowerCase();
      const summary = (group.summary || "").toLowerCase();
      const matchTitleOrSummary = title.includes(query) || summary.includes(query);
      if (matchTitleOrSummary) return true;

      // 匹配关联物理表
      const relatedData = group.related_data || [];
      const matchTable = relatedData.some((related) => {
        const dataset = (related.dataset || "").toLowerCase();
        const displayName = (related.display_name || "").toLowerCase();
        const tables = (related.tables || []).some((t) => t.toLowerCase().includes(query));
        return dataset.includes(query) || displayName.includes(query) || tables;
      });
      if (matchTable) return true;

      // 匹配推荐问题
      const questions = group.questions || [];
      const matchQuestion = questions.some((q) => {
        const label = (q.label || "").toLowerCase();
        const queryText = (q.query || "").toLowerCase();
        return label.includes(query) || queryText.includes(query);
      });
      if (matchQuestion) return true;

      return false;
    }

    return true;
  });
});

const formattedGeneratedAt = computed(() => {
  if (!props.payload.generated_at) return "";
  const date = new Date(props.payload.generated_at);
  if (Number.isNaN(date.getTime())) return props.payload.generated_at;
  return date.toLocaleString();
});

const handleRefreshClick = () => {
  isRefreshing.value = true;
  emit("refresh");
  // 防御性安全重置
  setTimeout(() => {
    isRefreshing.value = false;
  }, 30000);
};

// 监听 payload 的变化重置刷新动画与过滤状态
watch(
  () => props.payload,
  () => {
    isRefreshing.value = false;
    searchQuery.value = "";
    selectedTag.value = "All";
    activeTableDictionary.value = null;
    pinnedTableDictionary.value = null;
  },
  { deep: true }
);

const toggleGroup = (groupId: string) => {
  expandedGroups.value[groupId] = !expandedGroups.value[groupId];
};

const emitQuickQuestion = (query?: string) => {
  const text = String(query || "").trim();
  if (text) {
    emit("quick-question", text);
  }
};

const handleQuestionClick = (question: DatasetCapabilityQuestion, group: DatasetCapabilityGroup) => {
  const query = String(question.query || "").trim();
  if (!query) return;
  emit("record-question-click", {
    query,
    label: question.label,
    group_id: group.id,
  });
  emitQuickQuestion(query);
};

// 数据表数据字典快捷提问处理器
const handleQuickQuestionClick = (type: 'structure' | 'query' | 'recommend', table: string, related: DatasetCapabilityRelatedData) => {
  const physicalName = related.table_physical_names?.[table] || "";
  const tableWithPhysical = physicalName ? `‘${table}’（物理表名：${physicalName}）` : `‘${table}’`;

  let queryText = "";
  if (type === 'structure') {
    queryText = `说明数据表${tableWithPhysical}的字段结构和分析口径`;
  } else if (type === 'query') {
    queryText = `查询数据表${tableWithPhysical}最近10条明细数据`;
  } else if (type === 'recommend') {
    queryText = `根据数据表${tableWithPhysical}的字段定义，推荐 3 个最适合的业务分析提问。生成的问题以 \`- [🙋 推荐问题描述](quick:提问具体指令)\` 的格式输出这 3 个问题，以便我一键点击触发提问。`;
  }
  
  if (queryText) {
    emitQuickQuestion(queryText);
  }
  
  // 收起浮窗
  pinnedTableDictionary.value = null;
  activeTableDictionary.value = null;
};

// 图标与视觉渐变定义辅助函数
const getGroupVisuals = (index: number, title: string) => {
  const titleStr = title || "";
  
  if (titleStr.includes("分析") || titleStr.includes("监控") || titleStr.includes("日志") || titleStr.includes("诊断")) {
    return {
      bg: "bg-blue-50 dark:bg-blue-950/20",
      border: "border-blue-100 dark:border-blue-900/30",
      text: "text-blue-500 dark:text-blue-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z"/>
      </svg>`
    };
  }
  
  if (titleStr.includes("智能体") || titleStr.includes("Agent") || titleStr.includes("大模型") || titleStr.includes("AI")) {
    return {
      bg: "bg-violet-50 dark:bg-violet-950/20",
      border: "border-violet-100 dark:border-violet-900/30",
      text: "text-violet-500 dark:text-violet-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
      </svg>`
    };
  }
  
  if (titleStr.includes("数据") || titleStr.includes("看板") || titleStr.includes("报表") || titleStr.includes("门户")) {
    return {
      bg: "bg-emerald-50 dark:bg-emerald-950/20",
      border: "border-emerald-100 dark:border-emerald-900/30",
      text: "text-emerald-500 dark:text-emerald-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
      </svg>`
    };
  }

  // 默认根据 index 索引轮换
  const visuals = [
    {
      bg: "bg-blue-50 dark:bg-blue-950/20",
      border: "border-blue-100 dark:border-blue-900/30",
      text: "text-blue-500 dark:text-blue-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
      </svg>`
    },
    {
      bg: "bg-purple-50 dark:bg-purple-950/20",
      border: "border-purple-100 dark:border-purple-900/30",
      text: "text-purple-500 dark:text-purple-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>
      </svg>`
    },
    {
      bg: "bg-teal-50 dark:bg-teal-950/20",
      border: "border-teal-100 dark:border-teal-900/30",
      text: "text-teal-500 dark:text-teal-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
      </svg>`
    }
  ];
  return visuals[index % visuals.length];
};
</script>

<style scoped>
.animate-pulse-slow {
  animation: pulse 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: .85;
    transform: scale(0.98);
  }
}
</style>
