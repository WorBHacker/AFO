"""The window's plain words (stage 10.6, D37): error cards that say what to do, «Быстрее ↔ Качественнее» for a local
model's launch and for a model's variants, «Проверить систему» as rows."""

from app.ui import check_view, install_view, model_setup, rows
from hub import model_install as mi

GIB = 1024**3


def test_an_error_card_says_what_to_do():
    plain = rows.error_row("e1", "llama-swap: HTTP 500: CUDA error: out of memory")
    assert plain["title"] == "Не хватает памяти" and "поменьше" in plain["hint"] and "CUDA" in plain["text"]
    other = rows.error_row("e2", "что-то необычное")
    assert other["title"] == "Ошибка" and other["hint"] == ""
    said = install_view.with_hint("модель скачана, но не запустилась: llama-swap: 502 upstream command exited")
    assert said.startswith("модель скачана") and "Переустановите модель" in said.split("\n")[1]
    assert install_view.with_hint("") == "" and install_view.with_hint("неясно") == "неясно"
    from hub.gateway.vertex_provider import NO_ROLE

    role = rows.error_row("e3", f"vertex: {NO_ROLE}")  # stage 30: Google Cloud's role, not a wrong key
    assert role["title"] == "У аккаунта Google Cloud нет роли" and "Vertex AI User" in role["hint"]
    assert "IAM" in role["hint"]


def test_faster_or_better_for_a_local_model():
    draft = {"context": "32768", "gpu_mode": "auto", "gpu_n": "", "flash_attn": "on", "cache_type": "q4_0",
             "parallel": "1", "ttl_min": "0"}  # fmt: skip
    fast = model_setup.preset_draft("fast", draft, 131072)
    assert (fast["context"], fast["cache_type"], fast["gpu_mode"]) == ("8192", "q8_0", "all")
    assert model_setup.preset_of(fast, 131072) == "fast" and model_setup.preset_of(draft, 131072) == ""
    quality = model_setup.preset_draft("quality", draft, 40960)  # never past what the model was trained for
    assert (quality["context"], quality["cache_type"]) == ("40960", "f16")
    assert model_setup.preset_of(quality, 40960) == "quality" and fast["parallel"] == "1"  # the rest as it was


def test_faster_or_better_among_the_variants():
    variants = [{"quant": "Q2_K", "size": 6 * GIB, "bits": 2.9}, {"quant": "Q3_K_M", "size": 8 * GIB, "bits": 3.9},
                {"quant": "Q4_K_M", "size": 10 * GIB, "bits": 4.8}, {"quant": "Q6_K", "size": 13 * GIB, "bits": 6.6},
                {"quant": "Q8_0", "size": 17 * GIB, "bits": 8.5},
                {"quant": "BF16", "size": 32 * GIB, "bits": 16.0}]  # fmt: skip
    judged = {"Q2_K": "gpu", "Q3_K_M": "gpu", "Q4_K_M": "gpu", "Q6_K": "gpu", "Q8_0": "split", "BF16": "no"}
    assert mi.recommend(variants, judged) == "Q6_K"
    assert mi.extremes(variants, judged) == {"fast": "Q3_K_M", "quality": "Q8_0"}  # not the 2-bit, not 16-bit
    picks = ("", "fast", "", "", "quality", "")
    shown = install_view.variant_rows({"variants": [{**v, "pick": p} for v, p in zip(variants, picks, strict=True)]})
    assert [r["pick"] for r in shown] == ["", "быстрее", "", "", "качественнее", ""]


def test_the_system_check_as_rows():
    lines = [{"id": "hub", "title": "Хаб", "ok": True, "text": "работает"},
             {"id": "memory", "title": "Память", "ok": None, "text": "подкачка 4 ГБ", "advice": "увеличьте"},
             {"id": "llama-swap", "title": "llama-swap", "ok": False, "text": "не отвечает",
              "advice": "включите"}]  # fmt: skip
    shown = check_view.check_rows(lines)
    assert [(r["tone"], r["mark"]) for r in shown] == [("ok", "в порядке"), ("warn", "стоит взглянуть"),
                                                        ("error", "не работает")]  # fmt: skip
    assert check_view.summary(lines) == "Не работает: 1, стоит взглянуть: 1"
    assert check_view.summary(lines[:1]) == "Всё в порядке" and check_view.summary([]) == ""
