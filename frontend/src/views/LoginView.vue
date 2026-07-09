<script setup lang="ts">
import { Lock, User } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { zhCnMessages as messages } from "../locales/zh-CN";
import { useAuthStore } from "../stores/auth";

const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();
const activeTab = ref<"login" | "bootstrap">("login");
const form = reactive({
  username: "admin",
  displayName: messages.auth.defaultDisplayName,
  password: "",
});
const passwordDialogVisible = ref(false);
const pendingCurrentPassword = ref("");
const passwordForm = reactive({
  newPassword: "",
  confirmPassword: "",
});
const showResetAdmin = computed(() => route.query.resetAdmin === "1");

const redirectPath = () => {
  const redirect = route.query.redirect;
  return typeof redirect === "string" && redirect.startsWith("/") ? redirect : "/media-sources";
};

async function submitLogin() {
  try {
    await authStore.login(form.username, form.password);
    await handlePostLogin(form.password);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : messages.auth.loginFailed);
  }
}

async function submitBootstrap() {
  try {
    await authStore.bootstrapAndLogin(form.username, form.displayName, form.password);
    await handlePostLogin(form.password);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : messages.auth.bootstrapFailed);
  }
}

async function handlePostLogin(currentPassword: string) {
  if (authStore.currentUser?.mustChangePassword) {
    pendingCurrentPassword.value = currentPassword;
    passwordDialogVisible.value = true;
    return;
  }
  await router.replace(redirectPath());
}

async function submitChangePassword() {
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    ElMessage.error(messages.auth.passwordMismatch);
    return;
  }
  try {
    await authStore.changePassword(pendingCurrentPassword.value, passwordForm.newPassword);
    ElMessage.success(messages.auth.changePasswordSuccess);
    passwordDialogVisible.value = false;
    form.password = "";
    pendingCurrentPassword.value = "";
    passwordForm.newPassword = "";
    passwordForm.confirmPassword = "";
    await router.replace(redirectPath());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : messages.auth.changePasswordFailed);
  }
}

async function continueWithoutChange() {
  passwordDialogVisible.value = false;
  passwordForm.newPassword = "";
  passwordForm.confirmPassword = "";
  await router.replace(redirectPath());
}

async function resetAdminPassword() {
  try {
    await ElMessageBox.confirm(
      messages.auth.resetAdminPasswordConfirm,
      messages.auth.resetAdminPassword,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        type: "warning",
      },
    );
    await authStore.resetAdminPassword();
    ElMessage.success(messages.auth.resetAdminPasswordSuccess);
  } catch (error) {
    if (error === "cancel" || error === "close") {
      return;
    }
    ElMessage.error(error instanceof Error ? error.message : messages.auth.resetAdminPasswordFailed);
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-panel">
      <div class="login-brand">
        <div class="brand-logo login-logo">MR</div>
        <div>
          <h1>{{ messages.auth.title }}</h1>
          <p>{{ messages.auth.subtitle }}</p>
        </div>
      </div>

      <el-tabs v-model="activeTab" stretch>
        <el-tab-pane :label="messages.auth.loginTab" name="login">
          <el-form label-position="top" @submit.prevent>
            <el-form-item :label="messages.auth.username">
              <el-input v-model="form.username" :prefix-icon="User" :placeholder="messages.auth.usernamePlaceholder" />
            </el-form-item>
            <el-form-item :label="messages.auth.password">
              <el-input
                v-model="form.password"
                :prefix-icon="Lock"
                :placeholder="messages.auth.passwordPlaceholder"
                show-password
                type="password"
                @keyup.enter="submitLogin"
              />
            </el-form-item>
            <el-button class="login-submit" type="primary" :loading="authStore.loading" @click="submitLogin">
              {{ messages.auth.loginButton }}
            </el-button>
            <el-button
              v-if="showResetAdmin"
              class="login-reset"
              text
              :loading="authStore.loading"
              @click="resetAdminPassword"
            >
              {{ messages.auth.resetAdminPassword }}
            </el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane :label="messages.auth.bootstrapTab" name="bootstrap">
          <el-form label-position="top" @submit.prevent>
            <el-form-item :label="messages.auth.username">
              <el-input v-model="form.username" :prefix-icon="User" :placeholder="messages.auth.usernamePlaceholder" />
            </el-form-item>
            <el-form-item :label="messages.auth.displayName">
              <el-input v-model="form.displayName" :placeholder="messages.auth.displayNamePlaceholder" />
            </el-form-item>
            <el-form-item :label="messages.auth.password">
              <el-input
                v-model="form.password"
                :prefix-icon="Lock"
                :placeholder="messages.auth.passwordPlaceholder"
                show-password
                type="password"
                @keyup.enter="submitBootstrap"
              />
            </el-form-item>
            <el-button class="login-submit" type="primary" :loading="authStore.loading" @click="submitBootstrap">
              {{ messages.auth.bootstrapButton }}
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-dialog
      v-model="passwordDialogVisible"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :title="messages.auth.changePasswordTitle"
      width="420px"
    >
      <p class="password-dialog-message">{{ messages.auth.changePasswordMessage }}</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="messages.auth.newPassword">
          <el-input
            v-model="passwordForm.newPassword"
            :placeholder="messages.auth.newPasswordPlaceholder"
            show-password
            type="password"
          />
        </el-form-item>
        <el-form-item :label="messages.auth.confirmPassword">
          <el-input
            v-model="passwordForm.confirmPassword"
            :placeholder="messages.auth.confirmPasswordPlaceholder"
            show-password
            type="password"
            @keyup.enter="submitChangePassword"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="continueWithoutChange">{{ messages.auth.changePasswordLater }}</el-button>
        <el-button type="primary" :loading="authStore.loading" @click="submitChangePassword">
          {{ messages.auth.changePasswordNow }}
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px;
  background:
    linear-gradient(135deg, rgba(66, 118, 255, 0.10), transparent 32%),
    linear-gradient(315deg, rgba(35, 168, 133, 0.12), transparent 36%),
    var(--app-bg);
}

.login-panel {
  width: min(440px, 100%);
  padding: 28px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--panel-bg);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.12);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 22px;
}

.login-logo {
  flex: 0 0 auto;
}

.login-brand h1 {
  margin: 0 0 4px;
  font-size: 20px;
  line-height: 1.3;
}

.login-brand p {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.login-submit {
  width: 100%;
}

.login-reset {
  width: 100%;
  margin: 10px 0 0;
}

.password-dialog-message {
  margin: 0 0 14px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.6;
}
</style>
