<template>
  <div v-loading="loading">
    <!-- 强制改密时的醒目提示：用户是被「押」到这里来的，必须知道为什么 -->
    <el-alert
      v-if="mustChange"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 14px"
      title="管理员已重置你的密码，请先设置新密码"
      description="为保障账号安全，在完成修改前你将停留在本页面。修改完成后即可正常使用系统。"
    />

    <!-- 两栏而非三栏：身份与访问范围信息量小且相关，合并到左栏；
         改密表单独立右栏。三栏等宽会让信息量最少的「访问范围」独占一栏、
         下方留出大片空白（实测如此）。 -->
    <div class="profile-grid">
      <!-- ============ 左：我的身份 + 访问范围 ============ -->
      <div class="profile-col">
        <section class="panel">
          <div class="panel__head">
            <div class="panel__title">我的身份</div>
          </div>

          <!-- 身份卡：只放「一眼要看到」的信息（姓名 + 岗位）。
               姓名/署名/角色在下方表格与顶栏都有，此处不重复，
               否则同一个名字会出现 4 次（实测问题是信息冗余）。 -->
          <div class="id-card">
            <div class="id-card__avatar">{{ avatarText }}</div>
            <div class="id-card__main">
              <div class="id-card__name">{{ profile?.user.full_name || profile?.user.username }}</div>
              <div class="id-card__sub">
                <span class="mono">{{ profile?.user.username }}</span>
                <span class="sep">·</span>
                <span>{{ profile?.user.position || roleLabel }}</span>
              </div>
            </div>
          </div>

          <el-descriptions :column="2" size="small" border style="margin-top: 14px">
            <el-descriptions-item label="姓名" :span="2">
              {{ profile?.user.full_name || '（未填写）' }}
            </el-descriptions-item>
            <el-descriptions-item label="工号">
              <span class="mono">{{ profile?.user.employee_no || '（未填写）' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="执业证号">
              <span class="mono">{{ profile?.user.cert_no || '（未填写）' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="所属单位" :span="2">
              {{ profile?.user.org_name || '（未填写）' }}
            </el-descriptions-item>
            <el-descriptions-item label="所属部门">
              {{ profile?.user.department || '（未填写）' }}
            </el-descriptions-item>
            <el-descriptions-item label="职务/岗位">
              {{ profile?.user.position || '（未填写）' }}
            </el-descriptions-item>
            <el-descriptions-item label="签认署名">
              {{ profile?.user.signature || '（未填写，报告将使用姓名）' }}
            </el-descriptions-item>
            <el-descriptions-item label="联系方式">
              <template v-if="profile?.user.phone || profile?.user.email">
                <span v-if="profile?.user.phone">{{ profile.user.phone }}</span>
                <span v-if="profile?.user.phone && profile?.user.email" class="sep">·</span>
                <span v-if="profile?.user.email">{{ profile?.user.email }}</span>
              </template>
              <span v-else class="muted">（未填写）</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="panel__hint" style="margin-top: 10px">
            ⚠️ <strong>姓名 / 工号 / 单位 / 岗位 / 执业证号 / 签认署名 用于报告签认</strong>，
            由管理员维护。若信息有误，请联系系统管理员更正 ——
            报告上的责任人是审计依据，不应由本人随意修改。
          </div>
        </section>

        <section class="panel">
          <div class="panel__head">
            <div class="panel__title">我的访问范围</div>
          </div>

          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="角色">
              <el-tag size="small" effect="plain">{{ roleLabel }}</el-tag>
              <span class="muted small" style="margin-left: 8px">{{ roleDuty }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="授权项目">
              <template v-if="profile?.is_admin">
                <span class="muted">不受项目隔离限制（管理员）</span>
              </template>
              <template v-else-if="profile?.projects?.length">
                <div v-for="p in profile.projects" :key="p.id" class="proj-row">
                  <span>{{ p.name }}</span>
                  <span class="mono muted small">{{ p.code }}</span>
                </div>
              </template>
              <template v-else>
                <span class="tag tag--warn">未授权任何项目</span>
                <div class="panel__hint" style="margin-top: 4px">
                  你只能看到公共规范库与本人发起的数据。请联系管理员分配项目。
                </div>
              </template>
            </el-descriptions-item>
            <el-descriptions-item label="专业范围">
              <template v-if="profile?.user.specialties?.length">
                <el-tag
                  v-for="s in profile.user.specialties"
                  :key="s"
                  size="small"
                  effect="plain"
                  style="margin-right: 4px"
                >{{ s }}</el-tag>
              </template>
              <span v-else class="muted">（未设置）</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="panel__hint" style="margin-top: 10px">
            角色与项目授权由管理员分配。若需发起评估、人工复核或维护规范库，
            请联系管理员调整你的角色。
          </div>
        </section>
      </div>

      <!-- ============ 右：修改密码 ============ -->
      <section class="panel profile-col--aside">
        <div class="panel__head">
          <div class="panel__title">修改密码</div>
        </div>

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
          <el-form-item label="当前密码" prop="old_password">
            <el-input
              v-model="form.old_password"
              type="password"
              show-password
              placeholder="请输入当前使用的密码"
              :prefix-icon="Lock"
            />
          </el-form-item>

          <!-- 规则前置为「要求」，而不是等提交后当成报错弹出来。
               未输入时用中性色（灰），避免看起来像已经出错。 -->
          <div class="pw-rules" :class="{ 'is-idle': !form.new_password }">
            <div class="pw-rules__title">新密码要求</div>
            <div v-for="r in pwChecks" :key="r.label" class="pw-rule" :class="{ 'is-ok': r.ok }">
              <el-icon>
                <component :is="r.ok ? CircleCheckFilled : (form.new_password ? CircleClose : Minus)" />
              </el-icon>
              {{ r.label }}
            </div>
          </div>

          <el-form-item label="新密码" prop="new_password" style="margin-top: 12px">
            <el-input
              v-model="form.new_password"
              type="password"
              show-password
              placeholder="至少 8 位，需包含字母与数字"
              :prefix-icon="Lock"
            />
          </el-form-item>
          <el-form-item label="确认新密码" prop="confirm">
            <el-input
              v-model="form.confirm"
              type="password"
              show-password
              placeholder="再次输入新密码"
              :prefix-icon="Lock"
              @keyup.enter="submit"
            />
          </el-form-item>

          <el-button type="primary" style="width: 100%" :loading="saving" @click="submit">
            确认修改
          </el-button>
        </el-form>

        <div class="panel__hint" style="margin-top: 10px">
          修改成功后<strong>当前登录状态保持有效</strong>，无需重新登录（系统会重新签发凭证）。
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { CircleCheckFilled, CircleClose, Lock, Minus } from '@element-plus/icons-vue'
import { authApi, type ProfileResult } from '@/api'
import { tokenStore } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { ROLE_DUTIES, roleLabel as labelOfRole } from '@/utils/permissions'

/**
 * 个人中心。
 *
 * 存在的两个理由
 * --------------
 * 1. **自助修改密码**：此前系统没有这个能力，用户忘记或想更换密码只能找管理员重置。
 *    这既增加管理员负担，也让「要求下次登录后修改密码」这个标记形同虚设
 *    （用户无处可改，只能一直看提示横幅）。
 * 2. **确认自己的身份登记**：报告签认会用到姓名/单位/岗位/执业证号，
 *    信息错了用户要能发现 —— 否则报告上会一直挂着错误的责任人。
 *
 * ⚠️ 身份字段是**只读**的：它们是审计依据（报告上的责任人），
 * 不应由本人随意修改。需更正请联系管理员。
 */
const auth = useAuthStore()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const profile = ref<ProfileResult | null>(null)
const formRef = ref<FormInstance>()

const form = ref({ old_password: '', new_password: '', confirm: '' })

const roleLabel = computed(() => labelOfRole(profile.value?.user.role ?? auth.role))
const roleDuty = computed(() => ROLE_DUTIES[profile.value?.user.role ?? auth.role] ?? '')

/** 头像文字：优先姓名首字，回退账号首字母 */
const avatarText = computed(() => {
  const name = profile.value?.user.full_name || profile.value?.user.username || '?'
  return name.trim().slice(0, 1).toUpperCase()
})

/** 是否处于「必须先改密」状态 */
const mustChange = computed(() => Boolean(profile.value?.user.must_change_password))

/** 密码强度实时校验（与后端 PASSWORD_RULES 一致：长度≥8、含字母、含数字） */
const pwChecks = computed(() => {
  const pw = form.value.new_password
  return [
    { label: '长度至少 8 位', ok: pw.length >= 8 },
    { label: '包含字母', ok: /[A-Za-z]/.test(pw) },
    { label: '包含数字', ok: /\d/.test(pw) },
  ]
})

const rules: FormRules = {
  old_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value && value === form.value.old_password) {
          callback(new Error('新密码不能与当前密码相同'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value && value !== form.value.new_password) {
          callback(new Error('两次输入的密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

async function load() {
  loading.value = true
  try {
    profile.value = await authApi.profile()
    // 顺带刷新 store 里的用户（身份字段可能被管理员改过）
    auth.user = profile.value.user
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载个人信息失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  // 前端先拦一道，给出比后端更快的反馈（后端仍会再校验）
  if (pwChecks.value.some((c) => !c.ok)) {
    ElMessage.warning('新密码不满足强度要求：' + pwChecks.value.filter((c) => !c.ok).map((c) => c.label).join('、'))
    return
  }

  saving.value = true
  try {
    const result = await authApi.changePassword({
      old_password: form.value.old_password,
      new_password: form.value.new_password,
    })
    // ⚠️ 必须用返回的新 token 覆盖本地：后端重新签发了 token
    // （must_change_password 已清除）。不覆盖会陷入「改完还被拦」。
    tokenStore.set(result.access_token, result.refresh_token)
    auth.user = result.user

    ElMessage.success('密码已修改')
    form.value = { old_password: '', new_password: '', confirm: '' }
    formRef.value?.clearValidate()

    // 目标页：被强制改密的用户改完后回到本职落地页
    const target = auth.home
    ElMessage.info(`正在进入「${target}」…`)
    router.push({ name: target })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '修改失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 两栏布局：左栏（身份 + 访问范围）与右栏（改密）。
   用 2fr / 1fr 而非等宽 —— 左栏是信息展示（需要宽度放表格），
   右栏是表单（过宽反而降低可读性）。 */
.profile-grid {
  display: grid;
  grid-template-columns: minmax(0, 2.05fr) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.profile-col {
  display: grid;
  gap: 14px;
  min-width: 0;
}

/* 改密栏：粘性定位，左栏很长时不用来回滚动 */
.profile-col--aside {
  position: sticky;
  top: 0;
}

@media (max-width: 1080px) {
  .profile-grid {
    grid-template-columns: 1fr;
  }
  .profile-col--aside {
    position: static;
  }
}

/* 身份卡：报告签认会用到这些信息，做成醒目的「证件」样式 */
.id-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: 6px;
  background: var(--surface-alt, #fafbfc);
  border: 1px solid var(--line);
}

.id-card__avatar {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(145deg, #1d3a63, #2a5290);
}

.id-card__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-900);
}

.id-card__sub {
  margin-top: 3px;
  font-size: 12px;
  color: var(--ink-500);
}

.id-card__sub .sep {
  margin: 0 6px;
  color: var(--line);
}

.proj-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  line-height: 1.9;
}

/* 密码规则提示。
   未输入时用中性灰（.is-idle）—— 若一进页面就显示红色叉，
   用户会以为「已经出错了」，实际只是还没填。 */
.pw-rules {
  display: grid;
  gap: 3px;
  padding: 8px 10px;
  border-radius: 5px;
  background: var(--surface-alt, #fafbfc);
  border: 1px solid var(--line);
}

.pw-rules__title {
  font-size: 11.5px;
  color: var(--ink-500);
  margin-bottom: 2px;
}

.pw-rule {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-color-danger);
  line-height: 1.7;
}

.pw-rule.is-ok {
  color: var(--el-color-success);
}

.pw-rules.is-idle .pw-rule {
  color: var(--ink-300);
}

.pw-rules.is-idle .pw-rule.is-ok {
  color: var(--el-color-success);
}
</style>
