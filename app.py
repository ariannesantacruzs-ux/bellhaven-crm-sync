import json
import os
import re
import time
from difflib import SequenceMatcher
import requests
from bs4 import BeautifulSoup
import streamlit as st

API_BASE = "https://analyst-assessment-production.up.railway.app/api/v1"
WEB_BASE = "https://analyst-assessment-production.up.railway.app"
API_TOKEN = "bh_2ZT8WFHmusWK5Dt5yBnOpw"
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"
STATE_FILE = "review_decisions.json"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
}

st.set_page_config(page_title="Bellhaven CRM Sync & Review", layout="wide")


def norm_text(s):
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    replacements = {
        "street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
        "lane": "ln", "boulevard": "blvd", "court": "ct", "parkway": "pkwy",
        "rehabilitation": "rehab", "centre": "center", "healthcare": "health care",
        "senior living": "", "nursing": "", "assisted living": "", "memory support": ""
    }
    for k, v in replacements.items():
        s = re.sub(rf"\b{k}\b", v, s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a, b):
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def map_care_type(offerings):
    joined = " ".join(offerings).lower()
    if "rehab" in joined or "nursing" in joined:
        return "Skilled Nursing"
    if "memory" in joined:
        return "Memory Care"
    if "assisted" in joined:
        return "Assisted Living"
    return offerings[0] if offerings else "Assisted Living"


@st.cache_data(ttl=300)
def scrape_bellhaven_website():
    communities = []
    page = 1
    while True:
        url = f"{WEB_BASE}/communities?page={page}"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".cardgrid .card")
        if not cards:
            break
        for card in cards:
            link = card.select_one("h3 a")
            if not link:
                continue
            detail_href = link["href"]
            detail_resp = requests.get(f"{WEB_BASE}{detail_href}", timeout=15)
            dsoup = BeautifulSoup(detail_resp.text, "html.parser")
            name = dsoup.select_one("h1").get_text(strip=True)

            street, city, state, zip_code, admin, phone = "", "", "", "", "", ""
            offerings = []
            dts = dsoup.select("dl.detail dt")
            dds = dsoup.select("dl.detail dd")
            for dt, dd in zip(dts, dds):
                label = dt.get_text(strip=True).lower()
                if "address" in label:
                    lines = [x.strip() for x in dd.stripped_strings]
                    if len(lines) >= 2:
                        street = lines[0]
                        m = re.match(r"^(.*?),\s*([A-Z]{2})\s*(\d{5})", lines[1])
                        if m:
                            city, state, zip_code = m.group(1), m.group(2), m.group(3)
                elif "care" in label:
                    offerings = [b.get_text(strip=True) for b in dd.select(".badge")]
                elif "administrator" in label:
                    admin = dd.get_text(strip=True)
                elif "phone" in label:
                    phone = dd.get_text(strip=True)

            communities.append({
                "slug": detail_href.split("/")[-1],
                "name": name,
                "street": street,
                "city": city,
                "state": state,
                "zip": zip_code,
                "care_offerings": offerings,
                "mapped_care_type": map_care_type(offerings),
                "administrator": admin,
                "phone": phone,
                "url": f"{WEB_BASE}{detail_href}"
            })
        if f"page={page+1}" not in resp.text:
            break
        page += 1
    return communities


def fetch_all_crm_accounts():
    accounts = []
    page = 1
    while True:
        resp = requests.get(f"{API_BASE}/accounts", headers=HEADERS, params={"page": page, "page_size": 50}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = next((v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else data
        if not items:
            break
        accounts.extend(items)
        if len(items) < 50:
            break
        page += 1
    return accounts


def load_decisions():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_decision(proposal_id, status, payload_sent=None):
    decisions = load_decisions()
    decisions[proposal_id] = {
        "decision": status,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "payload": payload_sent
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(decisions, f, indent=2)
def generate_proposals(web_list, crm_accounts):
    proposals = []
    matched_crm_account_ids = set()

    sample = crm_accounts[0] if crm_accounts else {}
    billing_billing_street = next((k for k in ["billing_street", "address", "billing_street_address"] if k in sample), "billing_street")
    billing_billing_zip = next((k for k in ["billing_zip", "billing_zip_code", "postal_code"] if k in sample), "billing_zip")
    care_type = next((k for k in ["care_type", "care_offerings"] if k in sample), "care_type")

    facilities = [a for a in crm_accounts if "(parent account)" not in (a.get("name") or "").lower()]

    for web in web_list:
        candaccount_idates = []
        for acc in facilities:
            score = 0.0
            reasons = []
            same_billing_state = (acc.get("billing_state") or "").upper() == web["state"].upper()
            same_billing_city = norm_text(acc.get("billing_city")) == norm_text(web["city"])
            st_sim = similarity(acc.get(billing_billing_street), web["street"])
            nm_sim = similarity(acc.get("name"), web["name"])

            if same_billing_state and st_sim > 0.85:
                score += 0.55
                reasons.append(f"Address match ({int(st_sim*100)}%)")
            elif same_billing_city and same_billing_state:
                score += 0.25
                reasons.append("Same city and state")

            if nm_sim > 0.70:
                score += 0.45 * nm_sim
                reasons.append(f"Name similarity ({int(nm_sim*100)}%)")

            if score >= 0.55:
                candaccount_idates.append((score, acc, reasons))

        candaccount_idates.sort(key=lambda x: x[0], reverse=True)

        if not candaccount_idates:
            paccount_id = f"CREATE_{web['slug']}"
            proposals.append({
                "account_id": paccount_id,
                "type": "CREATE_NEW_ACCOUNT",
                "title": f"Create new account: {web['name']}",
                "web": web,
                "crm": None,
                "evaccount_idence": [
                    f"Listed on official website ({web['city']}, {web['state']} {web['zip']})",
                    "No matching account found in CRM."
                ],
                "action_summary": f"Crear cuenta activa bajo parent Bellhaven ({BELLHAVEN_PARENT_ID})",
                "sop_note": "New community with no prior CRM history.",
                "api_actions": [{
                    "method": "POST",
                    "endpoint": "/accounts",
                    "body": {
                        "name": web["name"],
                        "parent_id": BELLHAVEN_PARENT_ID,
                        billing_billing_street: web["street"],
                        "billing_city": web["city"],
                        "billing_state": web["state"],
                        billing_billing_zip: web["zip"],
                        care_type: web["mapped_care_type"],
                        "status": "Active",
                        "note": f"Added from Bellhaven website directory ({web['url']})."
                    }
                }]
            })
        else:
            best_score, best_acc, reasons = candaccount_idates[0]
            matched_crm_account_ids.add(best_acc["account_id"])

            for dup_score, dup_acc, dup_reasons in candaccount_idates[1:]:
                if dup_score >= 0.70 and dup_acc["account_id"] not in matched_crm_account_ids and dup_acc.get("status") != "Inactive":
                    matched_crm_account_ids.add(dup_acc["account_id"])
                    surv_rev = (best_acc.get("lifetime_revenue") or 0) + (best_acc.get("outstanding_ar") or 0)
                    dup_rev = (dup_acc.get("lifetime_revenue") or 0) + (dup_acc.get("outstanding_ar") or 0)
                    survivor, loser = (dup_acc, best_acc) if dup_rev > surv_rev else (best_acc, dup_acc)

                    paccount_id_dup = f"DUP_{loser['account_id']}_INTO_{survivor['account_id']}"
                    proposals.append({
                        "account_id": paccount_id_dup,
                        "type": "DUPLICATE_ACCOUNT",
                        "title": f"Duplicate detected: {loser['name']} -> {survivor['name']}",
                        "web": web,
                        "crm": loser,
                        "evaccount_idence": [
                            f"Both records point to {web['name']} ({web['street']}, {web['city']}).",
                            f"Surviving record: {survivor['account_id']} (Rev: ${survivor.get('lifetime_revenue', 0)}, AR: ${survivor.get('outstanding_ar', 0)})",
                            f"Losing duplicate: {loser['account_id']} (Rev: ${loser.get('lifetime_revenue', 0)}, AR: ${loser.get('outstanding_ar', 0)})"
                        ],
                        "action_summary": f"Marcar {loser['account_id']} como Inactive y apuntar duplicate_of_account a {survivor['account_id']}",
                        "sop_note": "CRM convention: no delete/merge; mark losing record Inactive and link duplicate_of_account.",
                        "api_actions": [{
                            "method": "PATCH",
                            "endpoint": f"/accounts/{loser['account_id']}",
                            "body": {
                                "status": "Inactive",
                                "duplicate_of_account": survivor["account_id"],
                                "note": f"Marked as duplicate of {survivor['account_id']} ({survivor['name']}) during Bellhaven directory reconciliation."
                            }
                        }]
                    })

            diffs = {}
            evaccount_idence_lines = reasons.copy()

            if best_acc.get("name") != web["name"]:
                diffs["name"] = web["name"]
                evaccount_idence_lines.append(f"Nombre en CRM: '{best_acc.get('name')}' -> Web: '{web['name']}'")

            if norm_text(best_acc.get(billing_billing_street)) != norm_text(web["street"]) and web["street"]:
                diffs[billing_billing_street] = web["street"]
                evaccount_idence_lines.append(f"CRM Street: '{best_acc.get(billing_billing_street)}' -> Web: '{web['street']}'")

            if best_acc.get("status") != "Active":
                diffs["status"] = "Active"
                evaccount_idence_lines.append(f"Status en CRM es '{best_acc.get('status')}', pero está activo en la web.")

            needs_reparent = best_acc.get("parent_id") != BELLHAVEN_PARENT_ID

            if needs_reparent:
                rev = float(best_acc.get("lifetime_revenue") or 0)
                ar = float(best_acc.get("outstanding_ar") or 0)
                evaccount_idence_lines.append(f"Current Parent: {best_acc.get('parent_id') or 'Ninguno'} -> Expected Parent: Bellhaven ({BELLHAVEN_PARENT_ID})")
                evaccount_idence_lines.append(f"CRM Financials -> Lifetime Revenue: ${rev:,.2f} | Outstanding AR: ${ar:,.2f}")

                if rev > 0 and ar > 0:
                    paccount_id = f"CHOW_{best_acc['account_id']}"
                    new_body = {
                        "name": web["name"],
                        "parent_id": BELLHAVEN_PARENT_ID,
                        billing_billing_street: web["street"],
                        "billing_city": web["city"],
                        "billing_state": web["state"],
                        billing_billing_zip: web["zip"],
                        care_type: web["mapped_care_type"],
                        "status": "Active",
                        "note": f"Created via CHOW SOP from legacy account {best_acc['account_id']}."
                    }
                    proposals.append({
                        "account_id": paccount_id,
                        "type": "CHOW_NEW_ACCOUNT",
                        "title": f"CHOW (Change of Ownership with Open AR): {web['name']}",
                        "web": web,
                        "crm": best_acc,
                        "evaccount_idence": evaccount_idence_lines,
                        "action_summary": "1) Crear cuenta nueva bajo Bellhaven. 2) Dejar cuenta vieja intacta y enlazar chow_current_account.",
                        "sop_note": "CHOW SOP TRIGGERED: Revenue > 0 and Outstanding AR > 0. Legacy account preserved for billing collections.",
                        "api_actions": [
                            {"method": "POST", "endpoint": "/accounts", "body": new_body, "step": "create_chow_target"},
                            {"method": "PATCH", "endpoint": f"/accounts/{best_acc['account_id']}", "body": {
                                "note": f"CHOW executed: preserved legacy account due to outstanding AR (${ar:,.2f}). Linked to new Bellhaven account."
                            }, "step": "link_chow_old"}
                        ]
                    })
                else:
                    paccount_id = f"REPARENT_{best_acc['account_id']}"
                    diffs["parent_id"] = BELLHAVEN_PARENT_ID
                    diffs["note"] = f"Re-parented to Bellhaven Senior Living ({BELLHAVEN_PARENT_ID}); zero outstanding AR/revenue conflict."
                    proposals.append({
                        "account_id": paccount_id,
                        "type": "REPARENT_AND_UPDATE",
                        "title": f"Asignar Parent Bellhaven + Actualizar: {web['name']}",
                        "web": web,
                        "crm": best_acc,
                        "evaccount_idence": evaccount_idence_lines,
                        "action_summary": f"Actualizar parent_id a Bellhaven y corregir campos ({', '.join(diffs.keys())})",
                        "sop_note": "✅ Sin conflicto de AR pendiente (Revenue=0 o AR=0): se actualiza la cuenta existente directamente.",
                        "api_actions": [{
                            "method": "PATCH",
                            "endpoint": f"/accounts/{best_acc['account_id']}",
                            "body": diffs
                        }]
                    })
            elif diffs:
                paccount_id = f"UPDATE_{best_acc['account_id']}"
                diffs["note"] = "Updated facility details to match official Bellhaven website directory."
                proposals.append({
                    "account_id": paccount_id,
                    "type": "FIELD_UPDATE",
                    "title": f"Update facility attributes: {web['name']}",
                    "web": web,
                    "crm": best_acc,
                    "evaccount_idence": evaccount_idence_lines,
"action_summary": f"Actualizar campos: {', '.join(diffs.keys())}",
"sop_note": "Parent ID is already correct; syncing attributes only.",
"api_actions": [{
"method": "PATCH",
"endpoint": f"/accounts/{best_acc['account_id']}",
"body": diffs
}]
})
    for acc in facilities:
        if acc.get("parent_id") == BELLHAVEN_PARENT_ID and acc["account_id"] not in matched_crm_account_ids and acc.get("status") == "Active":
            paccount_id = f"ORPHAN_{acc['account_id']}"
            rev = float(acc.get("lifetime_revenue") or 0)
            ar = float(acc.get("outstanding_ar") or 0)
            proposals.append({
                "account_id": paccount_id,
                "type": "MISSING_FROM_WEBSITE",
                "title": f"Missing from Bellhaven website: {acc.get('name')}",
                "web": None,
                "crm": acc,
                "evaccount_idence": [
                    f"Account {acc['account_id']} lists Bellhaven as parent in CRM, but is no longer listed among the 34 communities on the website.",
                    f"City/State: {acc.get('billing_city')}, {acc.get('billing_state')} | Revenue: ${rev:,.2f} | AR: ${ar:,.2f}"
                ],
                "action_summary": "Cambiar status a 'Needs Review' y dejar nota de auditoría.",
                "sop_note": "New owner unknown; flagging as 'Needs Review' instead of guessing a parent.",
                "api_actions": [{
                    "method": "PATCH",
                    "endpoint": f"/accounts/{acc['account_id']}",
                    "body": {
                        "status": "Needs Review",
                        "note": "Flagged for review: account lists Bellhaven as parent in CRM but is no longer listed on Bellhaven's public directory."
                    }
                }]
            })

    return proposals


def execute_proposal(proposal):
    results = []
    created_account_id = None
    for action in proposal["api_actions"]:
        method = action["method"]
        url = f"{API_BASE}{action['endpoint']}"
        body = dict(action["body"])
        if action.get("step") == "link_chow_old" and created_account_id:
            body["chow_current_account"] = created_account_id
        if method == "POST":
            r = requests.post(url, headers=HEADERS, json=body, timeout=15)
            r.raise_for_status()
            resp_json = r.json()
            created_account_id = resp_json.get("account_id")
            results.append(resp_json)
        elif method == "PATCH":
            r = requests.patch(url, headers=HEADERS, json=body, timeout=15)
            r.raise_for_status()
            results.append(r.json())
    return results


st.title("Bellhaven Senior Living — CRM Reconciliation & Review")
with st.spinner("Loading website directory and CRM data..."):
    web_communities = scrape_bellhaven_website()
    crm_accounts = fetch_all_crm_accounts()
    st.write("Muestra CRM:", crm_accounts[0] if crm_accounts else "vacío")
    all_proposals = generate_proposals(web_communities, crm_accounts)
    decisions = load_decisions()

pending = [p for p in all_proposals if p["account_id"] not in decisions]
completed = [p for p in all_proposals if p["account_id"] in decisions]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Web Communities", len(web_communities))
c2.metric("CRM Accounts", len(crm_accounts))
c3.metric("Pending Review", len(pending))
c4.metric("Decided (Audited)", len(completed))

tab1, tab2, tab3 = st.tabs(["Review Queue", "Audit History", "Web Directory"])

with tab1:
    if not pending:
        st.success("All proposals have been audited.")
    for p in pending:
        with st.container(border=True):
            st.subheader(p["title"])
            st.info(f"{p['type']} | {p['sop_note']}")
            for ev in p["evaccount_idence"]:
                st.write(f"- {ev}")
            with st.expander("View API Payload"):
                st.json(p["api_actions"])
            b1, b2, _ = st.columns([1.5, 1.5, 5])
            if b1.button("Approve & Apply", key=f"app_{p['account_id']}", type="primary"):
                try:
                    res = execute_proposal(p)
                    save_decision(p["account_id"], "APPROVED", res)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
            if b2.button("Reject", key=f"rej_{p['account_id']}"):
                save_decision(p["account_id"], "REJECTED")
                st.rerun()

with tab2:
    for p in completed:
        dec = decisions[p["account_id"]]
        st.write(f"{dec['decision']} — {p['title']} ({dec['timestamp']})")

with tab3:
    st.dataframe(web_communities, use_container_width=True)