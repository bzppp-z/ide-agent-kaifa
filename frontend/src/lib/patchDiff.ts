/** PatchDiff 的纯逻辑：把 unified diff 拆成左右两栏（before / after）。 */

export interface PatchLike {
  search: string;
  replace: string;
  before?: string;
  after?: string;
  diff?: string;
}

export interface PatchSides {
  left: string[];
  right: string[];
}

/**
 * 左侧只保留删除行与上下文，右侧只保留新增行与上下文。
 * diff 缺失或解析不出内容时，退回用 search / replace 原文。
 */
export function buildPatchSides(patch: PatchLike): PatchSides {
  const diff = patch.diff?.trim();
  if (diff) {
    const left: string[] = [];
    const right: string[] = [];
    for (const line of diff.split("\n")) {
      if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")) {
        continue;
      }
      if (line.startsWith("-")) {
        left.push(line.slice(1));
      } else if (line.startsWith("+")) {
        right.push(line.slice(1));
      } else if (line.startsWith(" ")) {
        left.push(line.slice(1));
        right.push(line.slice(1));
      }
    }
    if (left.length > 0 || right.length > 0) {
      return { left, right };
    }
  }
  return {
    left: patch.search.split("\n"),
    right: patch.replace.split("\n"),
  };
}
