<script setup lang="ts">
import { Lock, User } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
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

const redirectPath = () => {
  const redirect = route.query.redirect;
  return typeof redirect === "string" && redirect.startsWith("/") ? redirect : "/media-sources";
};

async function submitLogin() {
  try {
    await authStore.login(form.username, form.password);
    await router.replace(redirectPath());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : messages.auth.loginFailed);
  }
}

async function submitBootstrap() {
  try {
    await authStore.bootstrapAndLogin(form.username, form.displayName, form.password);
    await router.replace(redirectPath());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : messages.auth.bootstrapFailed);
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
</style>
