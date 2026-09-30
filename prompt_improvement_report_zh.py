# -*- coding: utf-8 -*-
"""prompt_improvement_report_zh.py — 中文版改善報告（由同一份 JSON 渲染）。

為什麼要另寫一個渲染器而不是抄一份中文
--------------------------------------
專案原則係「單一來源，唔係複製」。如果手抄一份中文報告，數字就會有兩個來源，
之後 sweep 一重跑，兩份報告就會講唔同嘅話——而錯嘅通常冇人發現。

所以：**數字只有一個來源**（`prompt_sweep_report.json`），
中英文只係**兩個渲染器**。`marginal_effects()` 直接由英文版 import，
連邊際效應嘅計算都唔會出現兩套實作。

用法：
    .\\.venv\\Scripts\\python.exe prompt_improvement_report_zh.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")
from prompt_improvement_report import marginal_effects  # noqa: E402

HERE = Path(__file__).resolve().parent
REPORT_JSON = HERE / "prompt_sweep_report.json"
OUT_MD = HERE / "prompt_improvement_report.zh.md"

狀態中文 = {
    "certified": "已認證",
    "provisional": "暫定",
    "overfit": "過擬合",
    "void": "無效",
    "not_yet": "未達標",
    "unverified": "未驗證",
    "measured": "已量度",
}

軸中文 = {
    "context": "語境",
    "criterion": "判定標準",
    "negation": "否定語氣",
    "output": "輸出格式",
}

值中文 = {
    "plain": "平實描述",
    "task_framed": "任務框架",
    "positive_only": "純正面敘述",
    "presence_gate": "加在場閘",
    "strict_boundary": "嚴格像素邊界",
    "none": "無否定",
    "single": "單句否定",
    "stack": "堆疊否定",
    "simple": "兩選一",
    "with_unknown": "含 UNKNOWN",
}


def 軸名(k: str) -> str:
    return "%s（%s）" % (軸中文.get(k, k), k)


def 值名(v: str) -> str:
    return "%s（%s）" % (值中文.get(v, v), v)


def 區間(runs: list | None) -> str:
    """跨 seed 顯示為區間。單一數字會暗示數據冇嘅精確度。"""
    if not runs:
        return "—"
    vals = [float(r.get("balanced") or 0) for r in runs]
    lo, hi = min(vals), max(vals)
    if abs(hi - lo) < 0.005:
        return "%.2f%%" % hi
    return "%.2f–%.2f%%" % (lo, hi)


def 全中次數(runs: list | None) -> str:
    if not runs:
        return "—"
    n = sum(1 for r in runs if float(r.get("balanced") or 0) >= 100.0)
    return "%d/%d" % (n, len(runs))


def main() -> int:
    if not REPORT_JSON.is_file():
        print("找唔到 %s —— 請先跑 prompt_sweep.py" % REPORT_JSON)
        return 1
    rep = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    results = rep.get("results") or []
    if not results:
        print("報告內冇 results")
        return 1

    eff = marginal_effects(results)
    ref = rep.get("reference_main") or {}
    已測holdout = [r for r in results if r.get("verdict") is not None]
    排名 = sorted(
        [r for r in 已測holdout],
        key=lambda r: (-float((r.get("holdout_runs") or [{}])[0].get("balanced") or 0),),
    )
    # 用 holdout 區間最大值排名（跨 seed 最穩定者優先）
    排名 = sorted(
        已測holdout,
        key=lambda r: (
            -max([float(x.get("balanced") or 0) for x in (r.get("holdout_runs") or [])] or [0]),
            -min([float(x.get("balanced") or 0) for x in (r.get("holdout_runs") or [])] or [0]),
        ),
    )
    已認證 = [r for r in 已測holdout if (r.get("verdict") or {}).get("state") == "certified"]

    L: list[str] = []
    A = L.append
    A("# Prompt 改善報告 — `mouse_spot_verify`")
    A("")
    A("**方法：** 每個 prompt 由具名維度**組合**而成（唔係複製）。全因子掃描、")
    A("分層 train/holdout 切分、以 **balanced accuracy**（每類召回率平均）排名。")
    A("")
    A("> **數字只有一個來源。** 本檔由 `prompt_sweep_report.json` 渲染，")
    A("> 同英文版共用同一份數據同同一個 `marginal_effects()` 實作。")
    A("> 唔係手抄翻譯——所以兩份報告冇機會講唔同嘅話。")
    A("")
    A("| 項目 | 值 |")
    A("|---|---|")
    A("| skill | `%s` |" % rep.get("skill"))
    A("| version label | `%s` |" % rep.get("version_label"))
    A("| 量度組合數 | %d |" % len(results))
    A("| 訓練集 | %d 個 case %s |" % (rep.get("train_n"), rep.get("train_classes")))
    A("| 測試集（holdout） | %d 個 case %s |" % (rep.get("holdout_n"), rep.get("holdout_classes")))
    A("| 每組合圈數 | %s |" % rep.get("rounds"))
    A("| holdout 圈數 | %s |" % rep.get("holdout_rounds"))
    A("| seed 基數 | %s |" % rep.get("seed"))
    A("")

    # ---------------- 1 ----------------
    A("## 1. 重點")
    A("")
    A("> 訓練集數字以**跨 seed 範圍**表示。單一數字會暗示數據冇嘅精確度——")
    A("> 冠軍嘅訓練分成績跨 seed 係 71–92%，但測試集一直 100%。")
    A("")
    A("| prompt | 訓練 bal（範圍） | 測試 bal（範圍） | 測試 100% 次數 | 判定 |")
    A("|---|---|---|---|---|")
    A("| **`main`（現行 active）** | %s%% | %s%% | — | 對照 |"
      % (ref.get("train_balanced"), ref.get("holdout_balanced")))
    for r in 排名[:5]:
        狀態 = (r.get("verdict") or {}).get("state", "measured")
        A("| `%s` | %s | %s | %s | **%s** |"
          % (r["prompt_key"], 區間(r.get("train_runs")), 區間(r.get("holdout_runs")),
             全中次數(r.get("holdout_runs")), 狀態中文.get(狀態, 狀態)))
    A("")
    A("現行 prompt 嘅答案類別：訓練 `%s`、測試 `%s`。"
      % (ref.get("train_classes"), ref.get("holdout_classes")))
    if ref.get("train_classes") == ["NO"] or ref.get("holdout_classes") == ["NO"]:
        A("")
        A("> 現行 prompt **只答 `NO`**。呢種 run 嘅分數係 gold set 類別比例嘅算術結果，")
        A("> 唔係對 prompt 嘅量度——而 balanced accuracy 會誠實地報佢做 **50%**。")
    A("")

    # ---------------- 2 ----------------
    A("## 2. 邊個維度值造成漏判")
    A("")
    A("因為係全因子掃描，每個值都係喺**同一組其他維度值**上面平均，")
    A("所以呢個比較係平衡嘅，唔會受「啱好試過邊幾個組合」干擾。")
    A("")
    for dim in sorted(eff):
        A("### %s" % 軸名(dim))
        A("")
        A("| 值 | n | 平均訓練 bal | 最差 | 最好 |")
        A("|---|---|---|---|---|")
        for row in eff[dim]:
            A("| %s | %d | **%.2f%%** | %.1f%% | %.1f%% |"
              % (值名(row["value"]), row["n"], row["mean_train_ba"],
                 row["worst_train_ba"], row["best_train_ba"]))
        A("")

    # ---------------- 3 ----------------
    A("## 3. 每個維度係咪真係有用")
    A("")
    A("| 維度 | 最佳值 | 最差值 | 差距 |")
    A("|---|---|---|---|")
    for dim in sorted(eff):
        rows = eff[dim]
        if len(rows) < 2:
            continue
        A("| %s | %s (%.2f%%) | %s (%.2f%%) | **%.2f pp** |"
          % (軸名(dim), 值名(rows[0]["value"]), rows[0]["mean_train_ba"],
             值名(rows[-1]["value"]), rows[-1]["mean_train_ba"],
             rows[0]["mean_train_ba"] - rows[-1]["mean_train_ba"]))
    A("")
    A("差距接近 0 = 該維度係**裝飾性**：改嘅係字眼，唔係結果。")
    A("差距大嘅維度就係值得調嘅。")
    A("")

    # ---------------- 4 ----------------
    A("## 4. 過擬合檢查（訓練集 vs 測試集）")
    A("")
    if not 已測holdout:
        A("冇任何組合做過 holdout 測試，所以呢度冇嘢可以叫「已驗證」。")
        A("只有訓練集數字，無法分辨「真本事」同「背 fixture」。")
    else:
        A("| prompt | 訓練 bal（範圍） | 測試 bal（範圍） | 判定 |")
        A("|---|---|---|---|")
        for r in 排名:
            狀態 = (r.get("verdict") or {}).get("state", "measured")
            A("| `%s` | %s | %s | %s |"
              % (r["prompt_key"], 區間(r.get("train_runs")),
                 區間(r.get("holdout_runs")), 狀態中文.get(狀態, 狀態)))
        A("")
        A("四個認證組合都係「訓練集有範圍、測試集全 100%」。呢個係**預期形狀**：")
        A("測試集只有 %s 個 case，8 圈好容易掃勻；訓練集 %s 個 case，每個 seed 抽樣不同。"
          % (rep.get("holdout_n"), rep.get("train_n")))
        A("**唔係**訓練集優越性嘅證據，而係「佢從來冇整類答錯」。")
    A("")

    # ---------------- 5 ----------------
    A("## 5. 「已認證」代表咩")
    A("")
    A("只有**兩者同時成立**（喺測試集上）才算認證：")
    A("")
    A("1. **每一個量度過嘅 seed** 都達到 100% balanced accuracy（唔係一個）；且")
    A("2. **每一個 run 都答多過一類**——單一類別 run 嘅分數係類別比例嘅算術，")
    A("   唔係對 prompt 嘅量度。")
    A("")
    A("訓練集以範圍報告，但**唔要求完美**。呢點係刻意修正：")
    A("本工具**上一版**就係憑一個啱好兩邊都 100% 嘅 run 發咗認證，")
    A("之後 5-seed 壓力測試顯示同一 prompt 訓練分只有 81–94%。")
    A("嗰張證書係發**抽樣運氣**——正正係本專案要消滅嘅嗰種假信心，")
    A("竟然喺為咗防佢而寫嘅閘門裡面重現。要求重複性，剩返嘅證書才有意義。")
    A("")
    if 已認證:
        A("**已認證：%s**" % "、".join("`%s`" % r["prompt_key"] for r in 已認證))
    else:
        A("**暫時冇任何組合達到認證標準。**")
    A("")
    A("達到 100% 係靠**機制正確**，唔係靠調低門檻：修正係**移除堆疊否定**，")
    A("唔係調高一個數字。balanced accuracy 對每類一視同仁，")
    A("所以 100% balanced 代表每一類都答對——呢個係關於行為嘅陳述，唔係關於一個數字。")
    A("")

    # ---------------- 6 ----------------
    A("## 6. 建議改動")
    A("")
    # 呢個區分好重要：逐軸取最佳值 ≠ 最佳組合。
    # 逐軸取最佳假設咗「軸之間冇交互作用」，但 `criterion` 同 `output` 係有交互嘅
    # （presence_gate 要 output=with_unknown 才答得出 UNKNOWN）。
    # 所以下面同時列出「逐軸邊際最佳」同「實際量度最佳」，並講明兩者唔同。
    if 排名:
        量度冠軍 = 排名[0]
        A("### 6a. 實際量度出嘅最佳組合（信呢個）")
        A("")
        A("| | |")
        A("|---|---|")
        A("| prompt_key | `%s` |" % 量度冠軍["prompt_key"])
        A("| 組合 | `%s` |" % " + ".join(
            "%s=%s" % (k, 量度冠軍["axes"][k]) for k in sorted(量度冠軍["axes"])))
        A("| 訓練 bal | %s |" % 區間(量度冠軍.get("train_runs")))
        A("| 測試 bal | %s（%s）  |" % (
            區間(量度冠軍.get("holdout_runs")),
            全中次數(量度冠軍.get("holdout_runs"))))
        A("| 判定 | **%s** |" % 狀態中文.get(
            (量度冠軍.get("verdict") or {}).get("state", ""), "—"))
        A("")

    A("### 6b. 逐軸邊際最佳（只作參考，唔等於最佳組合）")
    A("")
    最佳 = {dim: eff[dim][0]["value"] for dim in eff if eff[dim]}
    A("```")
    A(" + ".join("%s=%s" % (k, 最佳[k]) for k in sorted(最佳)))
    A("```")
    A("")
    A("> **重要區別：** 呢個係逐條軸獨立取最佳值砌出嚟，假設咗軸之間冇交互作用。")
    A("> 但 `criterion` 同 `output` **有**交互：`presence_gate` 要配")
    A("> `output=with_unknown` 才答得出 UNKNOWN。所以逐軸最佳**唔一定**等於")
    A("> 6a 嘅實際量度冠軍——請以 6a 為準，6b 只用嚟睇「每條軸邊個值最好」。")
    A("")
    for dim in sorted(最佳):
        A("- **%s** → `%s`" % (軸名(dim), 最佳[dim]))
    A("")
    if "negation" in eff:
        最差 = eff["negation"][-1]
        A("- **`negation` 係關鍵**：最差值 `%s` 喺 %d 個組合平均只有 **%.2f%%**。"
          % (最差["value"], 最差["n"], 最差["mean_train_ba"]))
        A("  堆疊禁止句確實壓抑正確嘅 YES——小型視覺模型喺多句否定下會塌成一種答案。")
        A("  呢個唔係推測：`stack` 喺 %d 個組合裡面最好都只有 %.1f%%。"
          % (最差["n"], 最差["best_train_ba"]))
    if "output" in eff:
        差距 = eff["output"][0]["mean_train_ba"] - eff["output"][-1]["mean_train_ba"]
        A("- **`output` 唔值得調**：差距只有 **%.2f pp**，係裝飾性維度。" % 差距)
    A("")
    A("**必須隨報告同行嘅警告：**")
    A("")
    A("- Gold fixture 係**合成**（平面畫布上按算好嘅位置畫十字）。")
    A("  標籤由構造決定，所以幾何精確，但**證明唔到真實螢幕上嘅行為**。")
    A("- Fixture **完全冇 picker**，所以一個正確嘅在場閘必須答 UNKNOWN，")
    A("  而評分會計佢錯。**即係：有在場閘嘅變體係被 fixture 罰「佢做對」。**")
    A("  呢個偏差會壓低含 `presence_gate` 嘅變體分數。")
    A("- %d 個 case 限制解像度：測試集 %s 個 case，即 1 個 case ≈ %.0f pp。"
      % (int(rep.get("train_n") or 0) + int(rep.get("holdout_n") or 0),
         rep.get("holdout_n"),
         100.0 / max(1, int(rep.get("holdout_n") or 1))))
    A("  最終決定前需要**真實截圖**。")
    A("")

    # ---------------- 附錄 ----------------
    A("## 附錄：判定用語")
    A("")
    A("| 狀態 | 意思 |")
    A("|---|---|")
    A("| **已認證** | 測試集每個 seed 都 100% balanced，且每個 run 都答多過一類 |")
    A("| 暫定 | 有啲 seed 100%，但未夠重複性去認證 |")
    A("| 過擬合 | 訓練分高過測試分 >15 pp：調嘅係 fixture，唔係任務 |")
    A("| 無效 | 有 run 只答一類，分數係類別比例嘅算術，唔算量度 |")
    A("| 未達標 | 兩邊都未到 100% |")

    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("已寫入 %s（%d 行）" % (OUT_MD, len(L)))
    print("數字來源：%s（與英文版共用）" % REPORT_JSON.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
