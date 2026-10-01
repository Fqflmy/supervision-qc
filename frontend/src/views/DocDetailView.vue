<template>
  <div v-loading="loading">
    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">{{ doc?.spec_code }} {{ doc?.spec_name }}</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <span class="tag" :class="`tag--${DOC_STATUS_TONES[doc?.status ?? ''] ?? 'muted'}`">
            {{ DOC_STATUS_LABELS[doc?.status ?? ''] ?? doc?.status ?? '-' }}
          </span>
          <el-button size="small" @click="router.back()">返回</el-button>
        </div>
      </div>
      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="发布单位">{{ doc?.issuer || '-' }}</el-descriptions-item>
        <el-descriptions-item label="专业">{{ doc?.specialty || '-' }}</el-descriptions-item>
        <el-descriptions-item label="层级">{{ doc?.region_level || '-' }}</el-descriptions-item>
        <el-descriptions-item label="图谱">{{ doc?.kg_built ? '已构建' : '未构建' }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">版本与解析状态</div>
        <div style="display: flex; gap: 8px">
          <el-button
            size="small"
            type="primary"
            :disabled="!auth.canWriteKb"
            :loading="parsing"
            @click="parse(false)"
          >
            解析并建索引
          </el-button>
          <el-button
            size="small"
            :disabled="!auth.canWriteKb"
            :loading="parsing"
            @click="parse(true)"
          >
            解析并构建图谱
          </el-button>
        </div>
      </div>
      <el-table :data="doc?.versions ?? []" size="small" empty-text="暂无版本">
        <el-table-column prop="version_label" label="版本" width="140" show-overflow-tooltip />
        <el-table-column prop="file_name" label="文件" min-width="200" show-overflow-tooltip />
        <el-table-column label="大小" width="100">
          <template #default="{ row }"><span class="mono small">{{ fmtSize(row.file_size) }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="tag" :class="row.parse_status === 'parsed' ? 'tag--ok' : row.parse_status === 'failed' ? 'tag--danger' : 'tag--muted'">
              {{ PARSE_STATUS_LABELS[row.parse_status] ?? row.parse_status }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="分块数" width="90" align="center">
          <template #default="{ row }"><span class="mono">{{ row.chunk_count }}</span></template>
        </el-table-column>
        <el-table-column label="当前版本" width="90" align="center">
          <template #default="{ row }">
            <span class="tag" :class="row.is_current ? 'tag--info' : 'tag--muted'">{{ row.is_current ? '是' : '否' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="160">
          <template #default="{ row }"><span class="small" style="color: var(--danger)">{{ row.parse_error || '-' }}</span></template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel">
      <div class="panel__head">
        <div class="panel__title">条款分块（{{ chunkTotal }}）</div>
        <div class="panel__hint">人工可修订后自动重建该分块索引（FR-KB-10 增量更新）</div>
      </div>
      <el-table :data="chunks" size="small" empty-text="暂无分块，请先解析">
        <el-table-column label="#" width="60" align="center">
          <template #default="{ row }"><span class="mono">{{ row.chunk_index }}</span></template>
        </el-table-column>
        <el-table-column label="条款号" width="100">
          <template #default="{ row }">
            <span class="mono">{{ row.clause_no || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="章节" width="180" show-overflow-tooltip>
          <template #default="{ row }"><span class="small">{{ row.chapter_path || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="页码" width="70" align="center">
          <template #default="{ row }"><span class="mono small">{{ row.page_no ?? '-' }}</span></template>
        </el-table-column>
        <el-table-column label="Token" width="80" align="center">
          <template #default="{ row }"><span class="mono small">{{ row.token_count ?? '-' }}</span></template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="360">
          <template #default="{ row }">
            <div class="chunk-cell">{{ row.content }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!auth.canWriteKb" @click="editChunk(row)">
              修订
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="display: flex; justify-content: flex-end; margin-top: 12px">
        <el-pagination
          v-model:current-page="chunkPage"
          :page-size="chunkPageSize"
          :total="chunkTotal"
          layout="total, prev, pager, next"
          @current-change="loadChunks"
        />
      </div>
    </div>

    <el-dialog v-model="editVisible" title="修订分块内容" width="640px">
      <el-form label-width="80px">
        <el-form-item label="条款号">
          <el-input v-model="editForm.clause_no" placeholder="如 5.3.3" />
        </el-form-item>
        <el-form-item label="章节">
          <el-input v-model="editForm.chapter_path" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="editForm.content" type="textarea" :rows="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingChunk" @click="saveChunk">保存并重建索引</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { kbApi, type Chunk, type SpecDocDetail } from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  DOC_STATUS_LABELS,
  DOC_STATUS_TONES,
  PARSE_STATUS_LABELS,
  fmtSize,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const docId = Number(route.params.id)
const doc = ref<SpecDocDetail | null>(null)
const chunks = ref<Chunk[]>([])
const chunkPage = ref(1)
const chunkPageSize = ref(20)
const chunkTotal = ref(0)
const loading = ref(false)
const parsing = ref(false)

const editVisible = ref(false)
const savingChunk = ref(false)
const editingId = ref<number | null>(null)
const editForm = reactive({ clause_no: '', chapter_path: '', content: '' })

async function loadDoc() {
  loading.value = true
  try {
    doc.value = await kbApi.getDoc(docId)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadChunks() {
  try {
    const result = await kbApi.listChunks(docId, chunkPage.value, chunkPageSize.value)
    chunks.value = result.items
    chunkTotal.value = result.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '分块加载失败')
  }
}

async function parse(buildKg: boolean) {
  parsing.value = true
  try {
    const result = await kbApi.parse(docId, buildKg)
    ElMessage.success(
      `解析完成：${result.ingest.chunk_count} 个分块` +
        (result.knowledge_graph ? `，图谱抽取条款 ${(result.knowledge_graph as Record<string, number>).chunks ?? 0} 条` : ''),
    )
    await Promise.all([loadDoc(), loadChunks()])
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '解析失败')
  } finally {
    parsing.value = false
  }
}

function editChunk(row: Chunk) {
  editingId.value = row.id
  editForm.clause_no = row.clause_no ?? ''
  editForm.chapter_path = row.chapter_path ?? ''
  editForm.content = row.content
  editVisible.value = true
}

async function saveChunk() {
  if (editingId.value === null) return
  savingChunk.value = true
  try {
    await kbApi.updateChunk(editingId.value, {
      content: editForm.content,
      clause_no: editForm.clause_no || null,
      chapter_path: editForm.chapter_path || null,
    } as Partial<Chunk>)
    ElMessage.success('已保存，该分块索引已重建')
    editVisible.value = false
    await loadChunks()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingChunk.value = false
  }
}

onMounted(async () => {
  await loadDoc()
  await loadChunks()
})
</script>

<style scoped>
.chunk-cell {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-700);
  white-space: pre-wrap;
  max-height: 96px;
  overflow-y: auto;
}
</style>
