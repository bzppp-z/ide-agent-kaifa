/** 轻量 className 合并（等价于 clsx，够用且零依赖）。 */
export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

/**
 * 逐段编码文件路径：保留 `/` 分隔符，只对每一段做 encodeURIComponent。
 * 整体编码会把 `/` 变成 `%2F`，后端无法解析。
 */
export function encodePath(path: string): string {
  return path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

/** 是否像绝对路径（Windows 盘符、UNC 或 POSIX 根）。 */
export function isAbsolutePath(path: string): boolean {
  const value = path.trim();
  if (!value) return false;
  return /^(?:[a-zA-Z]:[\\/]|\\\\)/.test(value) || value.startsWith("/");
}

/**
 * 路径过长时保留头部（盘符/根）与尾部，中间用省略号收拢。
 * 完整路径仍应作为 title 展示。
 */
export function compactPath(path: string, maxLength = 46): string {
  const value = path.trim();
  if (maxLength <= 1 || value.length <= maxLength) return value;
  const headLength = Math.min(10, Math.floor(maxLength / 3));
  const tailLength = maxLength - headLength - 1;
  return `${value.slice(0, headLength)}…${value.slice(-tailLength)}`;
}

/** 文件树头部展示的会话根：本地会话给完整路径，demo 会话给仓库名。 */
export function sessionRootLabel(
  localPath: string | null | undefined,
  demoRepo: string,
): string {
  return localPath?.trim() || `demo/${demoRepo}`;
}
