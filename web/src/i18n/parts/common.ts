/**
 * Shared components: the states every screen falls back to, and the code/diff viewer.
 *
 * `states.tsx` is where a comparison UI says *why* there is nothing to show, so these
 * strings carry more weight than their length suggests — "the record could not be parsed"
 * is a different fact from "the read API is not available", and a reader who gets the wrong
 * one looks in the wrong place.
 */
export const common = {
  en: {
    "common.retry": "Retry",
    "common.notFound": "Not found",
    "common.badRequest": "The request was refused",
    "common.unavailable": "The read API is not available",
    "common.parse": "The record could not be parsed",
    "common.loadFailed": "Could not load the data",
    "common.readOnly": "read-only",
    "common.noPatch": "No patch recorded",
    "common.noPatchBoth": "Neither side recorded a patch for this round",
    "common.diffHint":
      "A run directory only carries a diff for the rounds the platform saved one for (runs/<id>/diffs/round-<n>.patch).",
  },
  zh: {
    "common.retry": "重试",
    "common.notFound": "没有找到",
    "common.badRequest": "请求被拒绝",
    "common.unavailable": "读 API 不可用",
    "common.parse": "记录无法解析",
    "common.loadFailed": "数据加载失败",
    "common.readOnly": "只读",
    "common.noPatch": "没有记录 diff",
    "common.noPatchBoth": "两边这一轮都没有记录 diff",
    "common.diffHint":
      "run 目录只为平台保存过 diff 的那些轮次带上 diff(runs/<id>/diffs/round-<n>.patch)。",
  },
} as const;
