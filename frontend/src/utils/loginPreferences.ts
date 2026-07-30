export function resolvePasswordAutocomplete(rememberPassword: boolean): "current-password" | "off" {
  return rememberPassword ? "current-password" : "off";
}
