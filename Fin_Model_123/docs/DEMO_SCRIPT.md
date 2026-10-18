# 🎥 FinGraph AI — Hackathon Video Demonstration Script

This document provides a structured, 10-part walkthrough for recording the 3-to-5-minute hackathon demonstration video for **FinGraph AI** (Track: *Agents That Act on Connected Data*).

---

## Part 1: Introduction (0:00 - 0:30)
* **Visual:** FinGraph AI dashboard home screen displaying the clean top navigation (`Dashboard` | `AI Chat`) with no sidebars.
* **Speaker Script:**
  > "Welcome to FinGraph AI, a connected financial intelligence platform developed for the FalkorDB Hackathon.
  > While traditional financial applications store statements in flat relational tables or loose vector chunks, financial reality is fundamentally connected: companies belong to sectors, operate in industries, report metrics across specific fiscal periods, and derive numbers from audited public filings.
  > FinGraph AI places FalkorDB at the very core of financial reasoning to eliminate hallucinations and trace every insight back to its verified public source."

---

## Part 2: Why Financial Data Benefits from Graph Relationships (0:30 - 0:50)
* **Visual:** Diagram of the graph topology (`(:Company)-[:BELONGS_TO]->(:Sector)<-[:BELONGS_TO]-(:Company)` and `(:Company)-[:HAS_OBSERVATION]->(:FinancialObservation)-[:MEASURES]->(:FinancialMetric)`).
* **Speaker Script:**
  > "Consider questions like: 'Compare Apple and Microsoft revenue growth', or 'Which companies in Apple's sector have higher operating margins?'
  > In a traditional setup, answering this requires multiple fragmented API calls and complex ad-hoc joins.
  > In FalkorDB, this is a natural graph traversal. The graph connects companies to their observations, metrics, reporting periods, and source documents, enabling multi-hop reasoning with mathematical precision."

---

## Part 3: FalkorDB Engine Verification (0:50 - 1:15)
* **Visual:** Terminal split screen or FalkorDB status banner. Run `docker ps` and execute `python scripts/verify_graph.py`.
* **Speaker Script:**
  > "Here we see FalkorDB running as our primary database engine.
  > Let's run our verification script: the graph contains 7 companies across US and Indian markets (AAPL, MSFT, GOOGL, AMZN, NVDA, RELIANCE, TCS), 4 sectors, 8 financial metrics, 78 verified financial observations, and 16 official source documents.
  > All 8 relationship types are active and indexed in FalkorDB."

---

## Part 4: Interactive Graph Provenance Explorer (1:15 - 1:40)
* **Visual:** Navigate to the `AI Chat` page and expand the `🕸️ FalkorDB Connected Financial Knowledge & Provenance Explorer`.
* **Speaker Script:**
  > "On our AI Chat page, judges can inspect the live FalkorDB knowledge graph.
  > Let's select Apple and trace 'Total Revenue': the UI displays the exact traversal path:
  > Apple Inc. ➜ Total Revenue ➜ FY2024 Observation ($391.04B) ➜ SEC Form 10-K filing.
  > Clicking the filing link opens the official SEC EDGAR record directly, proving genuine provenance."

---

## Part 5: Feature 1 — Cross-Company Financial Comparison (1:40 - 2:20)
* **Visual:** In the chat input, type: `"Compare Apple's and Microsoft's revenue growth in 2024."`
* **Speaker Script:**
  > "Now let's ask our AI Agent Team to compare Apple and Microsoft.
  > Behind the scenes, the agent invokes `tool_compare_company_metrics`.
  > It traverses both Company nodes to their FY2024 revenue observations, retrieves $391.04 billion for Apple and $245.12 billion for Microsoft, and compares their performance using the connected facts retrieved directly from FalkorDB."

---

## Part 6: Feature 2 — Sector Peer Discovery (2:20 - 2:50)
* **Visual:** Type in chat: `"Which companies in Apple's sector have higher operating margins?"`
* **Speaker Script:**
  > "Next, sector-level discovery. Notice we didn't specify who Apple's peers are.
  > The agent executes `tool_compare_industry_peers`, traversing Apple's `BELONGS_TO` edge to the Technology sector, discovering Microsoft, NVIDIA, and TCS, and comparing their FY2024 operating margins.
  > The response accurately ranks NVIDIA at 54.1% and Microsoft at 44.6%, both outperforming Apple's 31.5% margin."

---

## Part 7: Feature 3 — Verifiable Evidence Tracing (2:50 - 3:20)
* **Visual:** Type in chat: `"Where did Apple's 2024 net income number come from? Show evidence."`
* **Speaker Script:**
  > "Now we demonstrate graph explainability.
  > The agent executes `tool_financial_evidence`, retrieving the specific Observation ID (`obs_AAPL_net_income_2024_annual`), period end date, and cites the audited Form 10-K filing with its official SEC archive URL.
  > No numbers are invented; every figure is grounded in a verified graph path."

---

## Part 8: Hard Dependency Demonstration (3:20 - 3:50)
* **Visual:** Switch to terminal and stop FalkorDB: `docker compose stop falkordb`. In the browser, ask the same graph question.
* **Speaker Script:**
  > "To verify that FalkorDB is strictly essential and not an optional decoration, let's stop FalkorDB.
  > When we ask the graph comparison question again, the application explicitly reports:
  > 'Graph-powered financial intelligence is temporarily unavailable because FalkorDB is offline.'
  > The system refuses to silently fabricate data.
  > Now let's restart FalkorDB: `docker compose start falkordb`. Reconnection instantly restores graph-powered intelligence."

---

## Part 9: AWS EC2 & Production Architecture (3:50 - 4:15)
* **Visual:** Show browser pointing to the live HTTPS domain or EC2 console, and show `docker compose -f docker-compose.prod.yml ps`.
* **Speaker Script:**
  > "For production deployment, FinGraph AI is orchestrated via Docker Compose with a Caddy reverse proxy on an AWS EC2 Ubuntu instance.
  > Caddy handles automatic TLS certificates and WebSocket proxying, while FalkorDB is shielded inside a private Docker bridge network with no public ports exposed."

---

## Part 10: Summary & Hackathon Contributions (4:15 - 4:45)
* **Visual:** Display GitHub repository, test suite passing (96/96 tests passed), and compliance documentation.
* **Speaker Script:**
  > "In summary, FinGraph AI demonstrates how FalkorDB transforms financial intelligence:
  > 1. Essential graph traversal for cross-company comparison and peer discovery.
  > 2. Verifiable provenance tracing back to official SEC and BSE filings.
  > 3. Zero-downtime, fully containerized AWS EC2 deployment.
  > 4. 100% test pass rate across 96 automated tests.
  > Thank you for reviewing FinGraph AI!"
