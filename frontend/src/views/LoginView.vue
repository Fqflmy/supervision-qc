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
        <div class="login-form__sub">请使用监理业务账号登录管理后台</div>
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
        <div class="small muted" style="margin-top: 18px; line-height: 1.9">
          初始账号：<span class="mono">admin / Admin@12345</span>（由 seed_data.py 创建，请及时修改）
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '' })

const features = [
  'Multi-Query + BM25/向量混合检索 + RRF 融合 + BGE 重排',
  'Neo4j 知识图谱：条款引用链追踪与冲突检测',
  'LangGraph 五阶段评估 Agent，内置防死循环收敛守卫',
  'LLM-as-Judge 自动质量评审，幻觉引用强制校验',
]

async function onSubmit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    const back = (route.query.redirect as string) || '/dashboard'
    router.push(back)
  } catch {
    /* 错误已写入 store，由模板展示 */
  }
}
</script>

<style scoped>
.mb-12 {
  margin-bottom: 12px;
}
</style>
