"""Create a self-contained, printable property comparison report in memory."""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any


CATEGORY_LABELS = {
    "investigate_low_price": "To investigate low price",
    "below_estimate": "Asking below estimate",
    "fairly_priced": "Fairly priced",
    "negotiation_candidate": "Slightly expensive",
    "likely_expensive": "Likely expensive",
    "insufficient_evidence": "Insufficient evidence",
    "does_not_match": "Does not match",
}


def category_label(value: str | None) -> str:
    return CATEGORY_LABELS.get(value, str(value or "").replace("_", " ").capitalize())


def money(value: Any) -> str:
    return "—" if value is None else f"S${float(value):,.0f}"


def number(value: Any, suffix: str = "") -> str:
    return "—" if value is None else f"{float(value):,.1f}{suffix}"


def listing_key(row: dict) -> str:
    """Return the source-neutral key shared by listings and their comparables."""
    return f"{row.get('source_site', '')}\x1f{row.get('run_id', '')}\x1f{row.get('listing_id', '')}"


def planning_summary(plan: dict | None) -> str:
    if not plan:
        return "<p>No buyer or seller planning result was attached.</p>"
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, dict):
        return "<p>The attached file was not recognised as a buyer or seller planning result.</p>"
    rows = []
    for name, values in scenarios.items():
        if not isinstance(values, dict):
            continue
        price = values.get("purchase_price_sgd", values.get("sale_price_sgd"))
        loan = values.get("loan_amount_sgd", values.get("outstanding_loan_sgd"))
        cash = values.get("total_cash_needed_sgd", values.get("estimated_cash_proceeds_sgd"))
        cpf = values.get("cpf_used_sgd", values.get("cpf_refund_from_sale_sgd"))
        rows.append(
            f"<tr><td>{html.escape(str(name).replace('_', ' ').title())}</td>"
            f"<td>{money(price)}</td><td>{money(loan)}</td><td>{money(cpf)}</td><td>{money(cash)}</td></tr>"
        )
    if not rows:
        return "<p>The attached plan contains no displayable scenarios.</p>"
    return "<table><thead><tr><th>Scenario</th><th>Price</th><th>Loan</th><th>CPF</th><th>Cash / proceeds</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def build_report(
    listings: list[dict],
    comparables: dict[str, list[dict]],
    *,
    report_reference: str = "",
    comparison_notes: str = "",
    planning_result: dict | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    if not listings:
        raise ValueError("Select at least one listing")
    if len(listings) > 4:
        raise ValueError("Compare no more than four listings in one report")
    generated_at = generated_at or datetime.now().astimezone()
    cards = []
    evidence_sections = []
    for listing in listings:
        listing_id = str(listing["listing_id"])
        comparison_key = listing_key(listing)
        warning = " Verify the unusually low asking price and listing details." if listing.get("verify_low_price") else ""
        cards.append(f"""
        <section class="listing">
          <h2>{html.escape(listing.get('title') or listing_id)}</h2>
          <p class="address">{html.escape(listing.get('address') or '')}</p>
          <dl>
            <dt>Category</dt><dd>{html.escape(category_label(listing.get('stakeholder_category')))}</dd>
            <dt>Asking price</dt><dd>{money(listing.get('asking_price_sgd'))}</dd>
            <dt>Research range</dt><dd>{money(listing.get('lower_estimate_sgd'))} – {money(listing.get('upper_estimate_sgd'))}</dd>
            <dt>Point estimate</dt><dd>{money(listing.get('point_estimate_sgd'))}</dd>
            <dt>Difference</dt><dd>{number(listing.get('asking_premium_discount_pct'), '%')}</dd>
            <dt>Inferred unit</dt><dd>{html.escape(listing.get('inferred_flat_type') or 'Unknown')}, {number(listing.get('floor_area_sqm'), ' sqm')}</dd>
            <dt>Comparable evidence</dt><dd>{int(listing.get('minimum_comparable_count') or 0)} minimum; {html.escape(listing.get('confidence_label') or '')}</dd>
            <dt>Recent local training sales</dt><dd>{html.escape(str(listing.get('recent_town_flat_training_sales') if listing.get('recent_town_flat_training_sales') is not None else 'Unavailable'))}</dd>
          </dl>
          <p>{html.escape(listing.get('category_reason') or '')}{html.escape(warning)} {html.escape(listing.get('valuation_note') or '')}</p>
          <p><a href="{html.escape(listing.get('listing_url') or '', quote=True)}">Open original listing</a></p>
        </section>""")
        comparable_rows = []
        seen = set()
        for comparable in comparables.get(comparison_key, []):
            key = comparable.get("comparable_transaction_id")
            if key in seen:
                continue
            seen.add(key)
            comparable_rows.append(
                f"<tr><td>{html.escape((comparable.get('comparable_block') or '') + ' ' + (comparable.get('comparable_street') or ''))}</td>"
                f"<td>{html.escape(comparable.get('sale_month') or '')}</td><td>{money(comparable.get('sale_price_sgd'))}</td>"
                f"<td>{number(comparable.get('floor_area_sqm'), ' sqm')}</td><td>{number(comparable.get('distance_m'), ' m')}</td></tr>"
            )
            if len(comparable_rows) == 5:
                break
        evidence_sections.append(
            f"<h3>{html.escape(listing.get('title') or listing_id)} — selected comparables</h3>"
            + ("<table><thead><tr><th>Comparable</th><th>Sale month</th><th>Sale price</th><th>Area</th><th>Distance</th></tr></thead><tbody>" + "".join(comparable_rows) + "</tbody></table>" if comparable_rows else "<p>No comparable details are available.</p>")
        )
    reference = f" — {html.escape(report_reference.strip())}" if report_reference.strip() else ""
    notes = f"<section><h2>Comparison notes</h2><p>{html.escape(comparison_notes).replace(chr(10), '<br>')}</p></section>" if comparison_notes.strip() else ""
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>HDB comparison report</title><style>
    body{{font-family:Arial,sans-serif;color:#172630;max-width:1100px;margin:32px auto;padding:0 20px;line-height:1.45}}h1{{margin-bottom:4px}}h2{{margin-bottom:4px}}.meta,.address{{color:#60717b}}.notice{{background:#eef7f4;border-left:4px solid #14745d;padding:12px;margin:20px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}.listing{{border:1px solid #d8e0e3;padding:16px;break-inside:avoid}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:5px}}dt{{color:#60717b}}dd{{margin:0;text-align:right}}table{{border-collapse:collapse;width:100%;margin-bottom:20px}}th,td{{border-bottom:1px solid #d8e0e3;padding:8px;text-align:left}}a{{color:#086c5d}}@media print{{body{{margin:0}}a{{color:inherit}}.listing{{border-color:#aaa}}}}
    </style></head><body><h1>HDB comparison report{reference}</h1><p class="meta">Generated {html.escape(generated_at.strftime('%d %B %Y, %H:%M %Z'))}</p><div class="notice">Research planning report only. It is not an HDB valuation, loan approval, investment recommendation or property inspection. Confirm the exact unit, floor, condition, lease, eligibility, financing and listing availability independently.</div><div class="grid">{''.join(cards)}</div><section><h2>Comparable-sale evidence</h2>{''.join(evidence_sections)}</section><section><h2>Attached buyer or seller plan</h2>{planning_summary(planning_result)}</section>{notes}<section><h2>Questions to verify</h2><ul><li>What is the exact floor range, flat model and remaining lease?</li><li>Is the asking price current, and has an option already been issued?</li><li>What defects, renovation needs, orientation, noise or extension conditions apply?</li><li>Do HFE, financing, EIP/SPR quota and CPF rules allow this purchase?</li></ul></section></body></html>"""
    return document.encode("utf-8")


def parse_planning_upload(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("The attached planning result must be a valid JSON file") from error
    if not isinstance(value, dict):
        raise ValueError("The attached planning result must contain one JSON object")
    return value
