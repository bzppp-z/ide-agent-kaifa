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
