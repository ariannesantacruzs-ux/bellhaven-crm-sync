

Bellhaven Senior Living — CRM Sync \& Review Pipeline



Automated daily reconciliation pipeline between Bellhaven Senior Living's public directory and Meridian CRM, featuring a human-in-the-loop Streamlit review queue.



Key Capabilities



• Automated Scraper \& Matcher: Extracts all 34 communities across paginated pages and matches them against CRM accounts via normalized street address and facility name similarity.

• CHOW Financial Guardrail (SOP): Checks lifetime\_revenue and outstanding\_ar before re-parenting. When both are > 0, preserves the legacy account for collections, creates a new Bellhaven account, and links them via chow\_current\_account.

• CRM Conventions \& Auditability: Marks losing duplicates Inactive with duplicate\_of\_account, flags missing communities as Needs Review, and logs audit notes on every API mutation.

• Idempotency: Persists all human approvals/rejections in review\_decisions.json so daily scheduled runs (daily\_sync.yml) never re-propose decided records.



