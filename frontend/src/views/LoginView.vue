<template>
  <div class="login-page">
    <section class="login-hero">
      <div class="login-hero__title">工程监理质量<br />智能评估系统</div>
      <p class="login-hero__desc">
        面向工程监理领域的规范知识管理与质量评估平台：多阶段 RAG 检索精准定位条款，
        知识图谱追踪引用链，LangGraph 评估 Agent 自动完成合规判定与报告生成。
      </p>
      <div class="login-hero__list">
        <div v-for="item in features" :key="item" class="login-hero__item">
          <span class="login-hero__dot"></span>{{ item }}
        </div>
      </div>
    </section>

    <section class="login-form-wrap">
      <div class="login-form">
        <div class="login-form__title">账号登录</div>
        <div class="login-form__sub">选择身份后登录，将进入该角色对应的界面</div>

        <!-- 身份选择：仅演示/开发环境显示（后端 demo-identities 控制） -->
        <div v-if="identities.length" class="identity">
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

        <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" size="large" placeholder="请输入用户名" clearable />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              placeholder="请输入密码"
              show-password
              @keyup.enter="onSubmit"
            />
          </el-form-item>
          <el-alert v-if="auth.error" :title="auth.error" type="error" :closable="false" show-icon class="mb-12" />
          <el-button type="primary" size="large" style="width: 100%" :loading="auth.loading" @click="onSubmit">
            登 录
          </el-button>
        </el-form>

        <div v-if="identities.length" class="login-form__hint">
          选择身份只是快速填入演示账号；<strong>登录后的权限完全由账号在服务端的角色决定</strong>，
          客户端无法自行提升。
        </div>
        <div v-else class="login-form__hint">
          请使用管理员分配的账号登录。若你尚未被授权任何项目，
          登录后只能看到公共规范库与本人发起的数据。
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { authApi, type DemoIdentity } from '@/api'
import { homeForRole, useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '', role: '' })
const identities = ref<DemoIdentity[]>([])

const features = [
  'Multi-Query + BM25/向量混合检索 + RRF 融合 + BGE 重排',
  'Neo4j 知识图谱：条款引用链追踪与冲突检测',
  'LangGraph 五阶段评估 Agent，内置防死循环收敛守卫',
  'LLM-as-Judge 自动质量评审，幻觉引用强制校验',
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
  try {
    const result = await authApi.demoIdentities()
    identities.value = result.enabled ? result.identities : []
    // 默认选中第一个身份，减少一次点击
    if (identities.value.length) pickIdentity(identities.value[0])
  } catch {
    // 取不到就不显示身份选择，不影响手工登录
    identities.value = []
  }
})
</script>

<style scoped>
.mb-12 {
  margin-bottom: 12px;
}

.identity {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 18px;
}

.identity__card {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
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
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.login-form__hint {
  margin-top: 16px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--el-text-color-secondary);
}
</style>
