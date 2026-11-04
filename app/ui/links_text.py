"""Links between projects as the window shows them (D25). No Qt here."""

from __future__ import annotations

from typing import Any

from common.i18n import N_, tr

KIND_LABELS = {
    "uses": N_("использует"),
    "provides": N_("предоставляет"),
    "shares": N_("общее"),
    "deploys_with": N_("развёртываются вместе"),
    "same_product": N_("части одного продукта"),
    "other": N_("связь"),
}


def project_links(links: list[dict[str, Any]], project_id: str) -> dict[str, list[dict[str, Any]]]:
    """The links of one project: confirmed ones ("→ back, использует (REST)") and proposals with their evidence."""
    confirmed, proposed = [], []
    for link in links:
        if project_id not in (link["a_id"], link["b_id"]):
            continue
        outgoing = link["a_id"] == project_id
        other = link["b_name"] if outgoing else link["a_name"]
        kind = tr(KIND_LABELS.get(link["kind"], link["kind"]))
        row = {
            "id": link["id"],
            "other": other,
            "arrow": "→" if outgoing else "←",
            "kind": kind,
            "description": link.get("description", ""),
            "title": (f"{link['a_name']} → {link['b_name']}: {kind}"),
            "evidence": link.get("evidence", []),
        }
        (confirmed if link["status"] == "confirmed" else proposed).append(row)
    return {"confirmed": confirmed, "proposed": proposed}
