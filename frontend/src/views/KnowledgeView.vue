<template>
  <div>
    <div class="panel">
      <div class="toolbar">
        <el-input v-model="filters.keyword" placeholder="规范编号 / 名称" clearable style="width: 220px" @keyup.enter="load" />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px" @change="load">
          <el-option v-for="(label, value) in DOC_STATUS_LABELS" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="filters.specialty" placeholder="专业" clearable style="width: 150px" @change="load">
          <el-option v-for="s in specialties" :key="s" :label="s" :value="s" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
        <div class="toolbar__spacer"></div>
        <el-button type="primary" :icon="Upload" :disabled="!auth.canWriteKb" @click="uploadVisible = true">
          上传规范
        </el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>

      <el-table v-loading="loading" :data="docs" size="default" empty-text="暂无规范文档，请先上传">
        <el-table-column prop="spec_code" label="规范编号" width="150">
          <template #default="{ row }"><span class="mono">{{ row.spec_code }}</span></template>
        </el-table-column>
        <el-table-column prop="spec_name" label="规范名称" min-width="240" show-overflow-tooltip />
        <el-table-column prop="specialty" label="专业" width="110" />
        <el-table-column label="层级" width="90">
          <template #default="{ row }">
            <span class="tag tag--muted">{{ REGION_LABELS[row.region_level] ?? row.region_level ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="tag" :class="`tag--${DOC_STATUS_TONES[row.status] ?? 'muted'}`">
              {{ DOC_STATUS_LABELS[row.status] ?? row.status }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="图谱" width="80" align="center">
          <template #default="{ row }">
            <span class="tag" :class="row.kg_built ? 'tag--ok' : 'tag--muted'">{{ row.kg_built ? '已建' : '未建' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="入库时间" width="150">
          <template #default="{ row }"><span class="small muted">{{ fmtTime(row.created_at, 'YYYY-MM-DD HH:mm') }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.id)">详情</el-button>
            <el-button
              link
              type="primary"
              size="small"
              :disabled="!auth.canWriteKb"
              :loading="busyId === row.id"
              @click="parseDoc(row)"
            >
              解析
            </el-button>
            <el-button
              v-if="row.status !== 'published'"
              link
              type="success"
              size="small"
              :disabled="!auth.canWriteKb"
              @click="publish(row, 'publish')"
            >
              发布
            </el-button>
            <el-button
              v-else
              link
              type="warning"
              size="small"
              :disabled="!auth.canWriteKb"
              @click="publish(row, 'unpublish')"
            >
              下线
            </el-button>
            <el-button
              link
              type="info"
              size="small"
              :disabled="!auth.canWriteKb || !row.kg_built"
              @click="openGraph(row)"
            >
              图谱
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="display: flex; justify-content: flex-end; margin-top: 14px">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="load"
        />
      </div>
    </div>

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadVisible" title="上传规范文档" width="560px" @closed="resetUpload">
      <el-form :model="uploadForm" label-width="92px">
        <el-form-item label="规范编号">
          <el-input v-model="uploadForm.spec_code" placeholder="如 GB 50204-2015（留空则用文件名）" />
        </el-form-item>
        <el-form-item label="规范名称">
          <el-input v-model="uploadForm.spec_name" placeholder="如 混凝土结构工程施工质量验收规范" />
        </el-form-item>
        <el-form-item label="专业">
          <el-select v-model="uploadForm.specialty" placeholder="请选择" clearable style="width: 100%">
            <el-option v-for="s in specialties" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="层级">
          <el-radio-group v-model="uploadForm.region_level">
            <el-radio-button value="national">国家</el-radio-button>
            <el-radio-button value="industry">行业</el-radio-button>
            <el-radio-button value="local">地方</el-radio-button>
            <el-radio-button value="enterprise">企业</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            drag
            multiple
            :auto-upload="false"
            :limit="50"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :file-list="fileList"
            accept=".pdf,.doc,.docx,.txt,.xlsx,.xls"
          >
            <el-icon style="font-size: 30px; color: var(--ink-300)"><UploadFilled /></el-icon>
            <div style="margin-top: 6px">拖拽文件到此处，或<em>点击选择</em></div>
            <template #tip>
              <div class="small muted" style="margin-top: 6px">支持 PDF / Word / TXT / Excel，单文件 ≤ 200MB</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!files.length" @click="doUpload">
          上传并入库
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type UploadFile, type UploadFiles } from 'element-plus'
import { Refresh, Upload, UploadFilled } from '@element-plus/icons-vue'
import { kbApi, type SpecDoc } from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  DOC_STATUS_LABELS,
  DOC_STATUS_TONES,
  fmtTime,
} from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

const REGION_LABELS: Record<string, string> = {
  national: '国家',
  industry: '行业',
  local: '地方',
  enterprise: '企业',
}

const specialties = ['通用', '结构工程', '地基基础', '装饰装修', '屋面工程', '给排水', '电气工程', '施工安全']

const docs = ref<SpecDoc[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const busyId = ref<number | null>(null)

const filters = reactive({ keyword: '', status: '', specialty: '' })

const uploadVisible = ref(false)
const uploading = ref(false)
const files = ref<File[]>([])
const fileList = ref<UploadFiles>([])
const uploadForm = reactive({
  spec_code: '',
  spec_name: '',
  specialty: '',
  region_level: 'national',
})

async function load() {
  loading.value = true
  try {
    const result = await kbApi.listDocs({
      page: page.value,
      page_size: pageSize.value,
      keyword: filters.keyword || undefined,
      status: filters.status || undefined,
      specialty: filters.specialty || undefined,
    })
    docs.value = result.items
    total.value = result.meta.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  filters.specialty = ''
  page.value = 1
  load()
}

function openDetail(id: number) {
  router.push({ name: 'doc-detail', params: { id } })
}

function openGraph(row: SpecDoc) {
  router.push({ name: 'graph', query: { doc: String(row.id), code: row.spec_code } })
}

async function parseDoc(row: SpecDoc) {
  busyId.value = row.id
  try {
    const result = await kbApi.parse(row.id, false)
    const info = result.ingest
    ElMessage.success(
      `解析完成：${info.chunk_count} 个分块，命名空间 ${info.namespace}` +
        (info.degraded_embedding ? '（Embedding 已降级为哈希向量）' : ''),
    )
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '解析失败')
  } finally {
    busyId.value = null
  }
}

async function publish(row: SpecDoc, action: 'publish' | 'unpublish' | 'abolish') {
  const actionLabel = action === 'publish' ? '发布' : action === 'unpublish' ? '下线' : '废止'
  try {
    await ElMessageBox.confirm(`确认${actionLabel}规范「${row.spec_code} ${row.spec_name}」？`, '操作确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    const result = await kbApi.publish(row.id, action)
    ElMessage.success(`${actionLabel}成功，当前状态：${DOC_STATUS_LABELS[result.status] ?? result.status}`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

function onFileChange(file: UploadFile, list: UploadFiles) {
  fileList.value = list
  files.value = list.map((item) => item.raw as File).filter(Boolean)
}

function onFileRemove(_file: UploadFile, list: UploadFiles) {
  fileList.value = list
  files.value = list.map((item) => item.raw as File).filter(Boolean)
}

function resetUpload() {
  files.value = []
  fileList.value = []
  uploadForm.spec_code = ''
  uploadForm.spec_name = ''
  uploadForm.specialty = ''
  uploadForm.region_level = 'national'
}

async function doUpload() {
  if (!files.value.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    files.value.forEach((file) => form.append('files', file))
    if (uploadForm.spec_code) form.append('spec_code', uploadForm.spec_code)
    if (uploadForm.spec_name) form.append('spec_name', uploadForm.spec_name)
    if (uploadForm.specialty) form.append('specialty', uploadForm.specialty)
    form.append('region_level', uploadForm.region_level)

    const result = await kbApi.upload(form)
    if (result.created.length) {
      ElMessage.success(`上传成功 ${result.created.length} 个文件，请执行「解析」建立索引`)
    }
    if (result.errors.length) {
      ElMessage.warning(result.errors.map((e) => `${e.file}: ${e.error}`).join('；'))
    }
    uploadVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

onMounted(load)
</script>
