<template>
  <div class="login-page">
    <!-- ============ 左侧：品牌与价值主张 ============ -->
    <section class="login-hero">
      <div class="login-hero__inner">
        <!-- 机构标识区：企业系统的惯例位置 -->
        <div class="login-brand">
          <div class="login-brand__mark">监理</div>
          <div class="login-brand__text">
            <div v-if="orgName" class="login-brand__org">{{ orgName }}</div>
            <div class="login-brand__system">{{ systemName }}</div>
          </div>
        </div>

        <div class="login-hero__headline">
          建设工程质量<br />智能评估与合规审查平台
        </div>

        <p class="login-hero__desc">
          面向建设单位、监理单位与施工单位的质量管理数字化平台。
          以现行国家与行业规范为判定依据，对施工资料与实体质量进行自动化合规审查，
          输出可追溯、可复核的评估结论，支撑质量责任落实与过程留痕。
        </p>

        <!-- 价值主张：面向业务方，不罗列技术栈 -->
        <div class="login-hero__values">
          <div v-for="item in values" :key="item.title" class="value-item">
            <div class="value-item__icon">
              <el-icon><component :is="item.icon" /></el-icon>
            </div>
            <div>
              <div class="value-item__title">{{ item.title }}</div>
              <div class="value-item__desc">{{ item.desc }}</div>
            </div>
          </div>
        </div>

        <!-- 底部：部署形态与数据合规（企业系统的可信度信息） -->
        <div class="login-hero__meta">
          <span class="meta-chip">私有化部署</span>
          <span class="meta-chip">数据本地留存</span>
          <span class="meta-chip">全链路审计留痕</span>
        </div>
      </div>
    </section>

    <!-- ============ 右侧：统一身份认证 ============ -->
    <section class="login-form-wrap">
      <div class="login-panel">
        <div class="login-panel__head">
          <h1 class="login-panel__title">统一身份认证</h1>
          <p class="login-panel__sub">请使用平台分配的账号登录</p>
        </div>

        <!-- 身份选择：仅演示/开发环境显示（后端 demo-identities 控制） -->
        <div v-if="identities.length" class="identity-block">
          <div class="identity-block__label">
            <el-icon><User /></el-icon>
            快捷身份（演示环境）
          </div>
          <div class="identity">
            <div
              v-for="item in identities"
              :key="item.role"
              class="identity__card"
              :class="{ 'is-active': form.role === item.role }"
              @click="pickIdentity(item)"
            >
              <div class="identity__head">
                <span class="identity__label">{{ item.label }}</span>
                <el-icon v-if="form.role === item.role" class="identity__check"><Check /></el-icon>
              </div>
              <div class="identity__desc">{{ item.description }}</div>
            </div>
          </div>
        </div>

        <el-form :model="form" label-position="top" class="login-form" @submit.prevent="onSubmit">
          <el-form-item label="用户名">
            <el-input
              v-model="form.username"
              size="large"
              placeholder="请输入用户名"
              clearable
              :prefix-icon="User"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              placeholder="请输入密码"
              show-password
              :prefix-icon="Lock"
              @keyup.enter="onSubmit"
            />
          </el-form-item>

          <div class="login-form__row">
            <el-checkbox v-model="form.remember">记住用户名</el-checkbox>
            <span class="login-form__link" @click="showHelp = true">登录遇到问题？</span>
          </div>

          <el-alert
            v-if="auth.error"
            :title="auth.error"
            type="error"
            :closable="false"
            show-icon
            class="mb-12"
          />

          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="auth.loading"
            @click="onSubmit"
          >
            登 录
          </el-button>
        </el-form>

        <!-- 安全提示：企业系统应明确告知账号安全责任 -->
        <div class="login-panel__notice">
          <el-icon><WarningFilled /></el-icon>
          <span>
            本系统涉及工程质量数据，操作全程留痕。请妥善保管账号，
            <strong>首次登录后请立即修改初始密码</strong>。
          </span>
        </div>

        <!-- 页脚：版权与备案（未配置则不渲染，避免出现虚假备案号） -->
        <div v-if="hasLegalFooter" class="login-panel__footer">
          <span v-if="copyright">{{ copyright }}</span>
          <span v-if="copyright && icpNumber" class="sep">|</span>
          <a
            v-if="icpNumber"
            class="icp"
            href="https://beian.miit.gov.cn/"
            target="_blank"
            rel="noopener noreferrer"
          >{{ icpNumber }}</a>
        </div>
        <div v-else class="login-panel__footer login-panel__footer--plain">
          <span>{{ systemName }}</span>
          <span class="sep">|</span>
          <span>{{ version }}</span>
        </div>
      </div>

      <el-dialog v-model="showHelp" title="登录遇到问题" width="460px">
        <div class="help">
          <p><strong>忘记密码 / 账号被锁定</strong></p>
          <p>请联系本项目的系统管理员，在「用户与授权」中为你重置密码或启用账号。</p>
          <p><strong>登录后看不到数据</strong></p>
          <p>说明你尚未被授权任何项目。请联系管理员为你分配项目后重新登录。</p>
          <p><strong>看不到某个功能菜单</strong></p>
          <p>菜单按角色显示。若需发起评估、人工复核或维护规范库，请联系管理员调整你的角色。</p>
        </div>
      </el-dialog>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim,
  Check,
  DocumentChecked,
  Lock,
  TrendCharts,
  User,
  WarningFilled,
} from '@element-plus/icons-vue'
import { authApi, systemApi, type DemoIdentity } from '@/api'
import { homeForRole, useAuthStore } from '@/stores/auth'
import { COPYRIGHT, ICP_NUMBER, ORG_NAME, SYSTEM_NAME, HAS_LEGAL_FOOTER } from '@/config/brand'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '', role: '', remember: true })
const identities = ref<DemoIdentity[]>([])
const showHelp = ref(false)
const version = ref('v1.0.0')

const orgName = ORG_NAME
const systemName = SYSTEM_NAME
const copyright = COPYRIGHT
const icpNumber = ICP_NUMBER
const hasLegalFooter = HAS_LEGAL_FOOTER

/**
 * 价值主张（面向业务方）。
 *
 * 刻意**不罗列技术栈**（Multi-Query / RRF / LangGraph 等）：
 * 技术名词对建设单位、监理工程师没有决策价值，反而显得像技术演示。
 * 企业系统应回答「解决什么管理问题」。
 */
const values = [
  {
    icon: DocumentChecked,
    title: '规范条款自动比对',
    desc: '覆盖国家现行标准，逐条判定符合性并给出依据',
  },
  {
    icon: Aim,
    title: '报告结论可复核',
    desc: '机器判定与人工裁定并列留存，结论由责任人签发',
  },
  {
    icon: TrendCharts,
    title: '质量数据可追溯',
    desc: '条款引用链完整记录，评估过程全程审计',
  },
]

/** 选身份 = 填入该身份对应的演示账号，并记住期望落地页 */
function pickIdentity(item: DemoIdentity) {
  form.role = item.role
  form.username = item.username
  form.password = item.password
}

async function onSubmit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  try {
    const user = await auth.login(form.username, form.password)
    ElMessage.success(`登录成功，欢迎 ${user.full_name || user.username}`)

    if (form.remember) {
      localStorage.setItem('supervision:last_username', form.username)
    } else {
      localStorage.removeItem('supervision:last_username')
    }

    // 落地页优先用 ?redirect（例如被踢回登录前的目标），
    // 否则按**服务端返回的真实角色**决定；若与所选身份不一致，以服务端为准。
    const redirect = route.query.redirect as string | undefined
    if (redirect && redirect !== '/' && !redirect.startsWith('/login')) {
      // 阻止跳到无权页面：路由守卫仍会二次拦截，这里先做一次体验层校验
      router.push(redirect)
      return
    }
    const target = homeForRole(user.role)
    if (form.role && form.role !== user.role) {
      ElMessage.info(`账号实际角色为「${user.role}」，已按该角色进入对应界面`)
    }
    router.push({ name: target })
  } catch {
    /* 错误已写入 store，由模板展示 */
  }
}

onMounted(async () => {
  // 回填上次登录的用户名（仅用户名，不保存密码）
  const last = localStorage.getItem('supervision:last_username')
  if (last) form.username = last

  try {
    const result = await authApi.demoIdentities()
    identities.value = result.enabled ? result.identities : []
    // 仅当没有回填用户名时才默认选中第一个身份，避免覆盖用户自己的输入
    if (identities.value.length && !form.username) pickIdentity(identities.value[0])
  } catch {
    // 取不到就不显示身份选择，不影响手工登录
    identities.value = []
  }

  // 页脚版本号取自后端（失败则用默认值，不打扰用户）
  try {
    const health = await systemApi.health()
    if (health.version) version.value = `v${health.version}`
  } catch {
    /* 保持默认 */
  }
})
</script>

<style scoped>
.mb-12 {
  margin-bottom: 12px;
}

/* ---------- 快捷身份 ---------- */
.identity-block {
  margin-bottom: 20px;
}

.identity-block__label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-700);
  margin-bottom: 10px;
}

.identity {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.identity__card {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 9px 11px;
  cursor: pointer;
  transition: all 0.15s;
  background: var(--el-fill-color-blank);
}

.identity__card:hover {
  border-color: var(--el-color-primary-light-5);
}

.identity__card.is-active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.identity__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.identity__label {
  font-size: 13px;
  font-weight: 600;
}

.identity__check {
  color: var(--el-color-primary);
  font-size: 13px;
}

.identity__desc {
  margin-top: 3px;
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--el-text-color-secondary);
}

/* ---------- 表单辅助行 ---------- */
.login-form__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.login-form__link {
  font-size: 12.5px;
  color: var(--el-color-primary);
  cursor: pointer;
}

.login-form__link:hover {
  text-decoration: underline;
}

/* ---------- 帮助对话框 ---------- */
.help p {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.help p:not(:first-child) {
  margin-top: 14px;
}

.help strong {
  color: var(--ink-900);
}
</style>
