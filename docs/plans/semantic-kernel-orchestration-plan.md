# Semantic Kernel orchestration and the clinical assistant: findings and plan

> 2026-11-12. Base: `main` `ed64e64`. **Status: owner approved K1–K6 as recommended on 2026-11-12 (K5 withdrawn, see §4). Nothing implemented. Azure works since the owner's settings change on 2026-11-12 (§3).**
> Research prototype, non-diagnostic. The rules engine stays the only urgency authority. A named human signs off.

## 1. What was asked

1. Analyse the architecture.
2. Wire Semantic Kernel into a live orchestration layer and test it with the Azure key.
3. Use it for clinical summarising and for storing medical information.
4. Build a TriZetto-style agentic clinical assistant.
5. Check whether Cognizant uses Azure, and what is best to use.

## 2. Where the product stands today

| Area | State | Evidence |
|---|---|---|
| Semantic Kernel | Installed (1.44.1). Used in two places: the Azure extraction provider, and a connection test in `services/kernel.py` | `azure_provider.py`, `kernel.py` |
| Plugins | Four plugin classes exist as a test harness. Nothing in the patient flow calls them | `services/plugins.py` |
| Orchestration | None. A route `/ai/orchestrate` was written by a teammate and **removed on 2026-11-10** (owner decisions D3 and D6) | `docs/integration/semantic-kernel-remediation-plan.md` |
| Why it was removed | It was a second path that produced urgency outside the rules engine, took unconstrained free text, wrote no audit record, and added another kernel entry point | same document, §3 |
| A rule in the tests | Only `services/kernel.py` may call `invoke` on a kernel | `tests/privacy/test_boundary.py` |
| Text extraction | Local Qwen3-4B by setting; Azure provider present but not selected | `.env`, `providers.py` |
| Summary | Template sentences from stored facts; no model writes it | `encounter_summary.py` |
| Storage | One SQLite file and a documents folder on this computer. Nothing is stored in Azure | `config.py` |

The architecture document (docs/03 §9) lists fifteen plugins on one kernel. That is a design sketch. The built system is a fixed pipeline of ordinary functions with a human check at each step.

## 3. Azure test on this computer (2026-11-12)

The kernel's own connection test was run with a fixed synthetic prompt.

- **Result: failed.** Azure answered `404 DeploymentNotFound`: "The API deployment for this resource does not exist."
- **What that means:** the endpoint was reached and the key was not refused. The **deployment name in the settings file does not match any deployment on that Azure OpenAI resource**.
- **Who can fix it:** only the owner, in the Azure portal: create a chat model deployment, or correct `AZURE_OPENAI_DEPLOYMENT_NAME`. It must support tool calling and structured output.
- Until this is fixed, nothing in this plan can be tested against Azure. No key or address was printed.
- **Retest after the owner's settings change (2026-11-12, afternoon): passed.** The kernel returned its fixed reply in 2.5 s. The endpoint is an Azure AI Foundry address (`*.ai.azure.com`), API version `2024-08-01-preview`.

## 4. Research: Cognizant, Azure and Semantic Kernel

| Finding | Source | Confidence |
|---|---|---|
| Cognizant's "TriZetto Assistant on Facets" uses **Azure OpenAI Service and Semantic Kernel**, announced March 2024 | [Cognizant news release](https://news.cognizant.com/2024-03-06-Cognizant-and-Microsoft-Collaborate-to-Power-the-Future-of-Healthcare-Administration-with-Generative-AI), [Microsoft Semantic Kernel blog](https://devblogs.microsoft.com/semantic-kernel/cognizant-microsoft-collaborate-to-power-healthcare-with-generative-ai) | High: first-party |
| In 2023 the two companies agreed to run Cognizant's healthcare software on Azure and to integrate TriZetto with Microsoft Cloud for Healthcare | [HIT Consultant](https://hitconsultant.net/2023/04/06/cognizant-to-integrate-trizetto-with-microsoft-cloud-for-healthcare) | Medium: trade press |
| In January 2026 they announced a multi-year partnership to build agentic AI into industry workflows, covering TriZetto | [TechPartner News](https://www.techpartner.news/news/cognizant-and-microsoft-partner-to-co-build-ai-solutions-622862) | Medium: one trade source; the release itself was not read |
| Microsoft Agent Framework reached 1.0 in April 2026 and is positioned as the successor to Semantic Kernel for agent work. Semantic Kernel is still maintained | [Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx), [Microsoft migration post](https://devblogs.microsoft.com/semantic-kernel/migrate-your-semantic-kernel-and-autogen-projects-to-microsoft-agent-framework-release-candidate/) | Medium: the official migration guide was not read |

What this supports, and what it does not:

- **Supported:** Azure OpenAI with Semantic Kernel is the stack Cognizant itself named for its TriZetto assistant. Choosing it is the strongest alignment available.
- **Not supported:** "Cognizant always uses Azure." The sources show a deep Microsoft partnership for TriZetto, not exclusivity.
- **Correction (2026-11-12): "TriZetto AI Gateway" is a real Cognizant product.** The first search missed it and this plan first said the name could not be found; that was wrong. Cognizant launched it on 6 August 2025 ([release](https://news.cognizant.com/2025-08-06-Cognizant-Debuts-TriZetto-R-AI-Gateway-to-Power-the-Next-Generation-of-AI-in-Healthcare)). Our documents use the right name and need no change. Decision K5 is withdrawn.
  - What Cognizant says it is: one secure access point for TriZetto software to reach AI agents, with client-defined governance, identity management, single sign-on, role-based access, telemetry, observability and cost transparency. It is the foundation for the TriZetto Assistant.
  - The release names **no** model provider, cloud or framework. So the Gateway itself is not evidence for Azure; the 2024 TriZetto Assistant announcement is.
  - In May 2026 Cognizant announced an interface that lets AI agents work directly with TriZetto Unify, and it also has a Palantir partnership for TriZetto. That is further reason not to say "always Azure".
  - **What this means for our design:** the fenced assistant in §6 lines up with what the Gateway advertises: governed tools, role-based access, a recorded trace of every agent action, and cost visibility. Those four words belong on the demo slide, each backed by something the product really does.
- **Mistral, Qdrant, Cloudflare (the mentor's suggestion):** none of them appears in Cognizant's own announcements. They add nothing to the alignment story.

## 5. The honest position

- A model that picks its own steps and decides things would weaken the product's strongest claim: rules set urgency, every value is checked, a person signs off. That is why the earlier orchestration route was removed.
- An assistant that can only **read** this visit's checked record through a fixed set of tools, whose every sentence is machine-checked against stored facts before a doctor sees it, with the tool calls shown, does not weaken that claim. It demonstrates it.
- That is the design below. It reverses owner decisions D3 and D6 of 2026-11-10, so it needs an explicit decision (K1).
- No plan can promise a win. The organisers' judging sheet has still not been seen.

## 6. Proposed design: a fenced clinical assistant

**One kernel.** A single factory builds the Semantic Kernel with the Azure chat service. The extraction provider and the assistant both use it. Cloud services register only when the cloud switch is on, as now.

**Tools the model may call (Semantic Kernel plugins, all read-only):**

| Tool | Returns | Built from |
|---|---|---|
| `get_visit_facts` | The visit's facts with their check state and source ids | `assemble_evidence` |
| `list_missing_information` | What is not recorded, danger signs first | `missing_information` |
| `list_conflicts` | Values where sources disagree | the summary's conflict rules |
| `list_waiting_for_check` | Values no person has checked | the summary's pending list |
| `get_urgency_record` | The rules engine's recorded urgency and reasons, as text to quote | the latest triage run |
| `suggest_follow_up_questions` | Questions from the fixed bank only | `question_bank` |

**What no tool can do:** write a triage run, change urgency, mark anything as checked, read another visit, or take free text from the screen.

**What the doctor can ask (v1): a closed list of buttons, no typing.**

- "Summarise this visit in plain sentences."
- "What should I check first?"
- "What is missing or unclear?"
- "What changed since the last summary?"

A closed list keeps the existing privacy rule that only two request fields carry free text.

**How an answer is produced:**

1. The model receives the question and the tool list. It chooses which tools to call (Semantic Kernel function calling).
2. Tool results go back to the model with identifiers already removed, as for extraction.
3. The model writes an answer where every sentence cites fact ids.
4. **Every sentence goes through the existing gate:** cited facts must exist, every number must be in them, unchecked values must carry their tag, no diagnostic or prescriptive wording. A failing sentence is dropped and counted, never repaired.
5. The doctor sees the answer, the dropped-sentence count, and the tool trace ("called: get_visit_facts, list_conflicts").

**Stored for each run (append-only):** question id, tools called in order, fact ids cited, model and prompt version, sentences kept and dropped, token counts for cost visibility, consent sequence, who asked. The audit log holds ids and counts only, never answer text.

**Where data lives:** unchanged. Records stay in the local database. Azure receives only the redacted facts of the one visit for the length of the call, only with the patient's AI consent, and for the demo only with the synthetic-data switch on. Moving storage to Azure (a managed database or a FHIR service) is a deployment topic for after the hackathon and is not in this plan.

**If Azure is not available:** the assistant is absent and says so. It never falls back to a fake answer. The fixed "readable draft" (step 3 below) can run on the local model; tool choosing on a 4B local model is not reliable enough to offer.

## 7. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| K1 | Reverse D3 and D6: allow a live Semantic Kernel layer again, under the fence in §6 | **Yes, with the fence.** Without it, no. |
| K2 | Which Azure model deployment to create | A current chat model with tool calling and structured output. The owner chooses by what the subscription has; cost per answer is a few cents at most on synthetic visits. |
| K3 | Questions: closed buttons only (v1), or a typed question box | **Closed buttons.** A typed box needs a new privacy review and input checks. |
| K4 | Stay on Semantic Kernel 1.44, or move to Microsoft Agent Framework | **Stay on Semantic Kernel.** It is installed, tested here, and is what Cognizant named. Say on a slide that Agent Framework is the migration path. |
| K5 | ~~Correct "TriZetto AI Gateway" to the real product name~~ | **Withdrawn 2026-11-12.** The name is correct (§4). |
| K6 | Model-written sentences in front of the doctor at all (this is N2 of the earlier plan, now on Azure) | **Yes, only through the gate, in its own labelled box. The template note stays the record.** |

Also needed: the hackathon date and the organisers' judging sheet.

## 8. Plan (tests first in every step; nothing starts before §3 is fixed and K1–K6 are answered)

### Step 0. Azure working

- Owner fixes the deployment (§3). The connection test must print its fixed reply.
- Record model name and API version in the capabilities view, never the key.

### Step 1. One kernel factory

- `services/kernel.py` becomes the only place a kernel is built. `AzureProvider` takes its chat service from it.
- The health view reports "Azure chat: ready / not ready" truthfully (today it says "not configured" even when the local model is ready, and "configured" when the deployment is wrong).
- The test that limits who may call a model is changed on purpose to name two files: the kernel and the assistant. The change is reviewed like a safety change.

### Step 2. Read-only tools

- The six tools of §6 as Semantic Kernel functions over existing code. Each takes only a visit id that the server supplies, never the model.
- Tests: no tool writes; a tool cannot be given another visit's id; consent and facility checks apply; outputs carry no identifiers.

### Step 3. Readable draft (fixed sequence, no tool choosing)

- One call writes plain sentences from the fact list. Each goes through the gate.
- Works on Azure, and on the local model when Azure is off.
- Shown beside the template note, labelled as model-written.

### Step 4. The assistant

- Closed question list, function calling, gate, stored run, audit event.
- Limits: at most a fixed number of tool calls per answer, a time limit, and a refusal when the model asks for a tool that is not on the list.
- Tests with a stubbed chat service: tool trace recorded; an ungrounded sentence is dropped; a diagnostic sentence is dropped; an urgency-changing sentence is dropped; a prompt hidden in a patient's words does not change the tool list.

### Step 5. Screen

- An "Assistant" panel on the medical officer's case screen: four buttons, the answer, the tool trace, dropped-sentence count, the model name, "not a diagnosis".
- Each answer sentence links to its fact, as the note's lines do.

### Step 6. Evaluation

- Corpus cases for the assistant on the stubbed service (mock): sentences supported, sentences dropped for the right reason, tool calls valid.
- A separate real-Azure run on synthetic visits, reported with denominators and labelled as such.

### Step 7. Documents and demo

- Architecture, privacy and security documents updated.
- A two-minute demo path: speak in Hindi, see symptoms with durations, submit triage, open the doctor's screen, press "What should I check first?", show the tool trace and one dropped sentence.

### Verification for every step

- Full backend and frontend suites, the skip gate, the corpus.
- The browser check extended, and a check in Chrome on the running app.

## 9. Risks

- **A model sentence that is wrong but grounded.** The gate checks that a sentence matches its facts, not that the facts are right. Unchecked facts stay tagged.
- **Latency.** A tool-calling answer is several model calls. The screen must show progress and a time limit.
- **Cost and quota.** Each answer costs money on Azure. The demo needs a tested quota.
- **Prompt injection through patient words.** Tool results are data, the tool list is fixed in code, and urgency cannot be reached. Tests cover it; it is reduced, not removed.
- **Scope.** Seven steps on top of the clinical-note plan (steps 3 to 7 of that plan are still open). Without the hackathon date, order cannot be set.

## 10. Not verified in this plan

- That a correctly named Azure deployment will work with tool calling and structured output on this subscription.
- The January 2026 partnership details beyond one trade article.
- The organisers' judging criteria.
- Any effort estimate. None is given.

## 11. Step 1 record: one Azure chat factory (2026-11-12)

**What changed**

- `app/ai/azure_chat.py` is now the only place an Azure chat service is built (a test scans the code for others). The Semantic Kernel, the extraction provider and the image backend all take their service from it.
- It lives in `app/ai`, not `app/services`, because the OCR package may not import `app.services` (that path reaches the triage engine; a boundary test caught the first version).
- One set of rules for every Azure user: cloud switch on, synthetic data only, complete credentials, and an endpoint on the host allow-list. Otherwise no service and a short reason code; never a fallback.
- **A gap closed:** before this, the kernel registered Azure without the host check, so a wrong address in the settings would have received the API key. Only the extraction and image paths checked the host.
- **Host allow-list widened** to `*.services.ai.azure.com` (Azure AI Foundry). Your endpoint uses it. You had already made the same edit by hand in the main checkout; this branch makes it in the code with tests.
- **Truthful status:** the app runs one fixed synthetic prompt through the kernel in the background at startup. `/health` now has `azure_chat` with `state` (`not_configured`, `not_checked`, `ready`, `failed`), a short reason code, the time and the latency. Before, a wrong deployment name still showed as `configured`. `python -m app.services.kernel` prints the same result instead of a traceback.

**Checks**

| Check | Result |
|---|---|
| Backend | 1643 passed, 14 skipped (opt-in live tests), 5 expected failures; skip gate ok, minimum 1568 → 1590, one more required module |
| New tests | 24 (factory rules, allowed hosts, one construction site, both backends on the factory, reason codes, health field, Foundry host in settings) |
| Real Azure, connection check | `ready in 2572 ms`; wrong deployment name gives `failed (deployment_not_found)`; an address off the list gives `not_configured (endpoint_not_allowed)` and sends nothing |
| Real Azure, running app | `/health` went from `not_checked` to `ready` (2482 ms) a few seconds after start |
| Real Azure, extraction | `gpt-4.1-mini`, nine synthetic sentences: terms 14 / 15, durations 11 / 11, denials 3 / 4, valid 9 / 9, 2.6 s each |

**In Chrome, on a scratch app using your Azure (synthetic input, before commit)**

| Check | Result |
|---|---|
| `/health` with your settings | `azure_chat: ready`, 2318 ms; extraction provider `azure`, model `gpt-4.1-mini` |
| Questions step, typed "I am suffering from fever since 2 days and my head is paining since 2 hours. I do not have fever chills. No vomiting." | fever 2 days; headache (said: head is paining) 2 hours; fever chills denied; vomiting denied; each agreed 3 of 3; the screen says a cloud model was used and only synthetic data may be |
| Wrong deployment name | `/health`: `failed (deployment_not_found)`. Extraction: `502 AI_ADAPTER_ERROR`, "no fallback provider was used", nothing stored |
| Address off the list | `/health`: `not_configured (endpoint_not_allowed)`, nothing sent. With Azure as the extraction provider the app refuses to start and names the allowed hosts |

**Not done in this step**

- The extraction setting still points at the local Qwen model. Switching to Azure is a separate decision.
- Semantic Kernel writes its own error log line when the startup check fails; that line can include the provider's error text. It never contains case data (the prompt is fixed).
- The test that limits who may call a model still names only `services/kernel.py`. It changes in step 4, when the assistant is added.
