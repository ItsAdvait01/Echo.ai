import { useCallback, useEffect, useRef, useState } from "react";

type Tab = "home" | "teach" | "review" | "library" | "execution";
type State = "idle" | "listening" | "learning" | "processing" | "awaiting_approval" | "executing" | "waiting_for_confirmation" | "paused" | "stopped" | "completed" | "failed" | "service-unavailable" | "no-match";
type Step = { id: string; order: number; action_type: string; target: Record<string, unknown>; expected_result: Record<string, unknown>; risk: string };
type Workflow = { id: string; name: string; description: string; trigger_phrases: string[]; steps: Step[]; uncertainty_notes: string[]; approved: boolean; created_at?: string };
type Draft = { name: string; description: string; trigger_phrases: string[]; steps: Step[]; uncertainty_notes: string[]; id?: string };
type Session = { session_id: string; state: string; message: string; event_count: number; events: Array<{ id: string; event_type: string; page_title: string; page_url: string; target_summary?: string; safe_value?: string }>; draft?: Draft | null };
type Run = { id: string; workflow_id: string; state: string; last_verified_step: number; started_at?: string; ended_at?: string };
type Execution = { run: Run; message: string; current_step?: Step | null; verification?: { step_id: string; step_order: number; passed: boolean; confidence: number; observed: Record<string, unknown>; message: string } | null };
type ApiResult<T> = { ok: true; data: T } | { ok: false; message: string; unavailable: boolean };
type Recognition = { lang: string; interimResults: boolean; maxAlternatives: number; onresult: ((event: { results?: ArrayLike<ArrayLike<{ transcript?: string }>> }) => void) | null; onerror: (() => void) | null; onend: (() => void) | null; start: () => void; stop: () => void };
type SpeechWindow = Window & { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };

const apiBase = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const titles: Record<State, string> = { idle: "Ready", listening: "Listening", learning: "Learning", processing: "Working", awaiting_approval: "Awaiting approval", executing: "Executing", waiting_for_confirmation: "Needs confirmation", paused: "Paused", stopped: "Stopped", completed: "Completed", failed: "Failed", "service-unavailable": "Text mode · offline", "no-match": "No matching workflow" };
const nav: Array<{ id: Tab; label: string; number: string }> = [{ id: "home", label: "Home", number: "01" }, { id: "teach", label: "Teach", number: "02" }, { id: "review", label: "Review", number: "03" }, { id: "library", label: "Library", number: "04" }, { id: "execution", label: "Execution", number: "05" }];

async function request<T>(path: string, method = "GET", body?: unknown): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${apiBase}${path}`, { method, headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(8000) });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("json") ? await response.json() : null;
    if (!response.ok) {
      const detail = data?.detail;
      const message = typeof detail === "string" ? detail : detail?.message || data?.message || `Request failed (${response.status}).`;
      return { ok: false, message, unavailable: response.status === 503 };
    }
    return { ok: true, data: data as T };
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return { ok: false, message: timedOut ? "The service took too long to respond." : "Echo API is unavailable. Text controls remain available, but changes will not be saved.", unavailable: true };
  }
}

function shortValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "Not reported by API";
  if (typeof value === "string") return value;
  if (typeof value === "object") return Object.values(value as Record<string, unknown>).map(String).join(" · ") || "Details recorded";
  return String(value);
}

export default function App() {
  const [tab, setTab] = useState<Tab>("home");
  const [state, setState] = useState<State>("idle");
  const [message, setMessage] = useState("Echo is ready. Tell it what you want to teach or continue.");
  const [online, setOnline] = useState<boolean | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [targetUrl, setTargetUrl] = useState("https://");
  const [consent, setConsent] = useState(false);
  const [run, setRun] = useState<Execution | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [apiNotice, setApiNotice] = useState("");
  const recognitionRef = useRef<Recognition | null>(null);

  const refreshWorkflows = useCallback(async () => {
    const result = await request<{ workflows: Workflow[]; count: number }>("/api/workflows");
    if (result.ok) { setWorkflows(result.data.workflows || []); setOnline(true); }
    else { setOnline(false); setApiNotice(result.message); }
  }, []);

  useEffect(() => {
    const speech = window as SpeechWindow;
    setVoiceSupported(Boolean(speech.SpeechRecognition || speech.webkitSpeechRecognition));
    void request<{ status: string }>("/api/health").then((result) => {
      setOnline(result.ok);
      if (!result.ok) { setState("service-unavailable"); setMessage(result.message); setApiNotice(result.message); }
    });
    void refreshWorkflows();
  }, [refreshWorkflows]);

  useEffect(() => {
    if (!session?.session_id || state !== "learning") return;
    const poll = window.setInterval(async () => {
      const result = await request<Session>(`/api/learning/${session.session_id}/events`);
      if (result.ok) setSession(result.data);
    }, 2500);
    return () => window.clearInterval(poll);
  }, [session?.session_id, state]);

  const startExecution = async (workflow: Workflow, resume = false) => {
    setSelectedWorkflow(workflow); setTab("execution"); setBusy(true); setState("processing");
    const resumeFrom = resume && run?.run.workflow_id === workflow.id ? run.run.last_verified_step : 0;
    const response = await request<Execution>("/api/executions", "POST", { workflow_id: workflow.id, resume_from_step: resumeFrom });
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); setApiNotice(response.message); return; }
    setRun(response.data); setState(response.data.run.state as State); setMessage(response.data.message || "Execution status received.");
  };

  const startTeach = async () => {
    if (!consent) { setMessage("Confirm observation consent before starting Teach Mode."); return; }
    try { new URL(targetUrl); } catch { setMessage("Enter a valid website URL before starting."); return; }
    setBusy(true); setState("processing");
    const response = await request<Session>("/api/learning/start", "POST", { target_url: targetUrl, consent_confirmed: true });
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); setApiNotice(response.message); return; }
    setSession(response.data); setState("learning"); setTab("teach"); setMessage(response.data.message); setConsent(false);
  };

  const stopTeach = async () => {
    if (!session) return;
    setBusy(true); setState("processing");
    const response = await request<Session>(`/api/learning/${session.session_id}/stop`, "POST", { generate_draft: true });
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); setApiNotice(response.message); return; }
    setSession(response.data);
    if (response.data.draft) {
      const received = response.data.draft;
      setDraft(received);
      const id = received.id || (response.data as Session & { workflow_id?: string; draft_id?: string }).workflow_id || (response.data as Session & { draft_id?: string }).draft_id || null;
      setDraftId(id);
      setTab("review"); setState("awaiting_approval");
      setMessage(id ? "Review the proposed workflow before saving." : "Draft received. The API did not provide a workflow ID, so it cannot yet be approved or rejected through the current contract.");
    } else { setState(response.data.state as State); setMessage(response.data.message); }
  };

  const approveDraft = async (approved: boolean) => {
    if (!draft || !draftId) { setMessage("Approval is blocked because the current API response has no workflow ID. A1 needs to return and stage the draft before this action can be saved."); return; }
    setBusy(true);
    const response = await request<{ workflow: Workflow; message: string }>(`/api/workflows/${draftId}/approve`, "POST", { approved, name_override: draft.name });
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); setApiNotice(response.message); return; }
    setDraft(null); setDraftId(null); setState(approved ? "idle" : "idle"); setMessage(response.data.message || (approved ? "Workflow approved and saved." : "Workflow rejected."));
    if (approved) await refreshWorkflows(); else setTab("home");
  };

  const handleCommand = async (text: string, source: "voice" | "text" = "text") => {
    const cleaned = text.trim(); if (!cleaned) return;
    setCommand(cleaned); setBusy(true); setState("processing"); setMessage(`Understanding: “${cleaned}”`);
    const response = await request<{ intent: string | null; transcript: string; reply_text: string; source: string }>("/api/voice/command", "POST", { transcript: cleaned, source });
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); setApiNotice(response.message); return; }
    const { intent, reply_text: reply } = response.data;
    if (source === "voice" && "speechSynthesis" in window) window.speechSynthesis.speak(new SpeechSynthesisUtterance(reply));
    if (!intent) { setState("no-match"); setMessage(reply || "I couldn't match that command."); return; }
    if (intent === "teach.start") { setTab("teach"); setState("idle"); setMessage("Teach Mode is ready. Review the site and explicitly approve observation to start."); return; }
    if (intent === "teach.stop") { if (session) await stopTeach(); else { setState("idle"); setMessage("There is no active teaching session to stop."); } return; }
    if (intent === "workflow.execute") {
      if (workflows.length === 1) { await startExecution(workflows[0], true); return; }
      setTab("library"); setState(workflows.length ? "idle" : "no-match"); setMessage(workflows.length ? "Choose which approved workflow to continue." : "No approved workflows are saved yet. Teach one first."); return;
    }
    if (intent === "execution.pause") { await controlExecution("pause"); return; }
    if (intent === "execution.stop") { await controlExecution("stop"); return; }
    if (intent === "status.get") { setState(run?.run.state as State || (session ? "learning" : "idle")); setMessage(run?.message || (session ? `Teach session is ${session.state}; ${session.event_count} event(s) reported.` : `${workflows.length} approved workflow(s) are available.`)); return; }
    setState("no-match"); setMessage(reply || "That voice intent is not mapped to an available action.");
  };

  const controlExecution = async (action: "pause" | "stop") => {
    if (!run?.run.id) { setState("idle"); setMessage("There is no active execution to control."); return; }
    setBusy(true);
    const response = await request<Execution>(`/api/executions/${run.run.id}/${action}`, "POST");
    setBusy(false);
    if (!response.ok) { setState(response.unavailable ? "service-unavailable" : "failed"); setMessage(response.message); return; }
    setRun(response.data); setState(action === "stop" ? "stopped" : response.data.run.state as State); setMessage(response.data.message);
  };

  const beginListening = () => {
    const speech = window as SpeechWindow; const RecognitionCtor = speech.SpeechRecognition || speech.webkitSpeechRecognition;
    if (!RecognitionCtor) { setVoiceSupported(false); setMessage("Voice recognition is unavailable in this browser. Type a command below."); return; }
    const recognition = new RecognitionCtor(); recognition.lang = "en-US"; recognition.interimResults = false; recognition.maxAlternatives = 1;
    recognition.onresult = (event) => { const transcript = event.results?.[0]?.[0]?.transcript?.trim(); setListening(false); if (transcript) void handleCommand(transcript, "voice"); };
    recognition.onerror = () => { setListening(false); setMessage("Microphone access failed. You can still type commands below."); };
    recognition.onend = () => setListening(false); recognitionRef.current = recognition; setListening(true); setState("listening"); setMessage("Listening for a command…"); recognition.start();
  };

  const statusKey = state in titles ? state : "idle";
  const activeStep = run?.current_step;
  const verified = run?.run.last_verified_step ?? 0;
  const onSubmitCommand = (event: React.FormEvent<HTMLFormElement>) => { event.preventDefault(); void handleCommand(command, "text"); };

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#home" onClick={(event) => { event.preventDefault(); setTab("home"); }}><span className="brand-mark">e</span><span>echo<span className="brand-dot">.</span>ai<small>PERSONAL WORKFLOW AGENT</small></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map((item) => <button key={item.id} className={`nav-item ${tab === item.id ? "active" : ""}`} onClick={() => setTab(item.id)}><span className="nav-number">{item.number}</span>{item.label}{item.id === "review" && draft ? <i className="nav-indicator" /> : null}</button>)}</nav>
      <div className="sidebar-bottom"><div className="mini-orbit"><span /></div><p>Teach once.<br /><b>Remember intelligently.</b></p><small>Execute through voice.</small></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div><p className="eyebrow">YOUR PERSONAL WORKFLOW AGENT</p><h1>{nav.find((item) => item.id === tab)?.label || "Echo"}</h1></div><div className="top-actions"><span className={`connection ${online === false ? "offline" : ""}`}><i />{online === null ? "Connecting" : online ? "API connected" : "API offline"}</span><span className={`state-pill state-${statusKey}`}><i />{titles[statusKey]}</span></div></header>
      {(apiNotice || online === false) && <div className="notice" role="status"><b>Text and button controls are available.</b><span>{apiNotice || "Connect the Echo API to save workflows and execute actions."}</span><button onClick={() => { setApiNotice(""); void refreshWorkflows(); }}>Retry</button></div>}
      <section className="command-card"><div className="command-heading"><div><span className="eyebrow">VOICE COMMAND</span><p>{voiceSupported ? "Speak naturally. Echo will confirm what it understood." : "Voice is unavailable here; use the text command fallback."}</p></div><button className={`mic ${listening ? "listening" : ""}`} onClick={listening ? () => { recognitionRef.current?.stop(); setListening(false); setState("idle"); } : beginListening} aria-label={listening ? "Stop listening" : "Start voice command"}>{listening ? "■" : "●"}</button></div><form className="command-form" onSubmit={onSubmitCommand}><label className="sr-only" htmlFor="command-input">Type a voice command</label><input id="command-input" value={command} onChange={(event) => setCommand(event.target.value)} placeholder="Try “teach me this course” or “continue my workflow”…" /><button disabled={busy || !command.trim()}>{busy ? "Working…" : "Send command"}<span>↗</span></button></form><p className="command-hint">Try: “Start teaching” · “Continue my Python course” · “Pause” · “What’s my status?”</p></section>
      {session && state === "learning" && <div className="global-observation" role="status"><span className="pulse-dot" /><span><b>Teach observation active</b><small>Session {session.session_id} · {session.event_count} event(s) reported</small></span><button className="danger" disabled={busy} onClick={() => void stopTeach()}>{busy ? "Stopping…" : "■ Stop & review"}</button></div>}

      {tab === "home" && <section className="screen"><div className="welcome-grid"><article className="welcome panel"><span className="eyebrow">THE WORKFLOW LOOP</span><h2>Teach it once.<br /><em>Pick up where you left off.</em></h2><p>Echo observes a task you demonstrate, turns it into a plan you approve, then resumes it with visible checkpoints.</p><div className="lifecycle">{["Teach", "Learn", "Approve", "Save", "Retrieve", "Execute", "Verify"].map((label, index) => <span key={label}><i>{String(index + 1).padStart(2, "0")}</i>{label}{index < 6 && <b>→</b>}</span>)}</div></article><article className="teach-callout panel"><div className="callout-icon">↗</div><span className="eyebrow">START A WORKFLOW</span><h3>Show Echo how you do it</h3><p>Choose a page, give permission, and demonstrate the task. You can stop observation at any time.</p><button className="primary" onClick={() => setTab("teach")}>Open Teach Mode <span>→</span></button><div className="ambient-orb" /></article></div>
        <div className="section-title"><div><span className="eyebrow">SAVED MEMORY</span><h2>Your approved workflows</h2></div><button className="link-button" onClick={() => setTab("library")}>Open library →</button></div>
        <WorkflowCards workflows={workflows} onRun={(workflow) => void startExecution(workflow, true)} onReview={() => setTab("library")} />
      </section>}

      {tab === "teach" && <section className="screen"><div className="page-intro"><span className="eyebrow">TEACH MODE · CONSENT FIRST</span><h2>Demonstrate a task</h2><p>Observation starts only after your explicit permission. Echo watches the chosen browser session; stop is always available here.</p></div><div className="teach-layout"><article className="panel form-panel"><div className="panel-title"><span className="step-badge">01</span><div><h3>Set the observation scope</h3><p>Enter the page you plan to use.</p></div></div><label className="field-label" htmlFor="target-url">Website URL</label><input id="target-url" className="text-field" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} placeholder="https://course.example.com" disabled={Boolean(session && state === "learning")} /><div className="consent-box"><label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} disabled={Boolean(session && state === "learning")} /><span><b>I authorize Echo to observe this task.</b><small>Only start a session when you are ready. Never enter passwords or payment details while observing.</small></span></label></div>{session && state === "learning" ? <div className="active-observation"><span className="pulse-dot" /><div><b>Observation is active</b><small>Session {session.session_id} · {session.event_count} event(s) reported · {session.events?.[0]?.page_url || targetUrl}</small></div><button className="danger" disabled={busy} onClick={() => void stopTeach()}>{busy ? "Stopping…" : "■ Stop & review"}</button></div> : <button className="primary" disabled={busy || !consent} onClick={() => void startTeach()}>{busy ? "Starting…" : "● Start observation"}</button>}<p className="privacy-note">Echo’s current API does not expose pause/resume controls for teaching. Stop ends observation and requests a draft.</p></article><article className="panel observed-panel"><span className="eyebrow">LIVE SESSION</span><h3>{session?.state === "learning" ? "Watching your demonstration" : "No active session"}</h3><p>{session?.message || "After consent, session status and captured safe event summaries will appear here."}</p><div className="event-list">{session?.events?.length ? session.events.slice(-5).map((event) => <div className="event-row" key={event.id}><i>✓</i><span><b>{event.event_type}</b><small>{event.target_summary || event.page_title || event.page_url}</small></span><time>{shortValue(event.safe_value)}</time></div>) : <div className="empty-state"><span>◉</span><b>Events appear during an active session</b><small>No sample activity is shown as live observation.</small></div>}</div></article></div></section>}

      {tab === "review" && <section className="screen"><div className="page-intro"><span className="eyebrow">WORKFLOW REVIEW</span><h2>Review what Echo learned</h2><p>Check every action and expected result before approving a workflow for reuse.</p></div>{draft ? <article className="panel review-panel"><div className="review-title"><span className="step-badge">02</span><div><span className="eyebrow">DRAFT · NOT YET SAVED</span><h3>{draft.name}</h3><p>{draft.description || "No description returned."}</p></div><span className="draft-badge">Needs your approval</span></div><div className="review-meta"><span><b>Trigger phrases</b>{draft.trigger_phrases?.length ? draft.trigger_phrases.join(" · ") : "Not reported by API"}</span><span><b>Uncertainties</b>{draft.uncertainty_notes?.length ? draft.uncertainty_notes.join(" · ") : "None reported"}</span></div><div className="step-list">{draft.steps?.map((step) => <div className="review-step" key={step.id}><span className="step-number">{step.order}</span><div><b>{step.action_type}</b><small>Target: {shortValue(step.target)}</small></div><div className="expected"><b>Expected result</b><small>{shortValue(step.expected_result)}</small></div><span className={`risk risk-${step.risk}`}>{step.risk} risk</span></div>)}</div>{!draftId && <div className="contract-warning"><b>Approval is waiting on the API contract.</b><span>The current stop-session response returns a draft without an ID, and the backend does not stage it for approval. A1 must return/stage a workflow ID before these controls can persist a decision.</span></div>}<div className="review-actions"><button className="secondary" disabled={busy} onClick={() => void approveDraft(false)}>Reject draft</button><button className="primary" disabled={busy} onClick={() => void approveDraft(true)}>{busy ? "Saving…" : "Approve & save"}<span>→</span></button></div></article> : <Empty title="No draft to review" text="Stop a teaching session to request a workflow draft. Only an approved workflow can be executed." action="Go to Teach Mode" onClick={() => setTab("teach")} />}</section>}

      {tab === "library" && <section className="screen"><div className="page-intro"><span className="eyebrow">APPROVED MEMORY</span><h2>Workflow library</h2><p>Only workflows returned by the API appear here. Progress is shown only when reported by an execution.</p></div><WorkflowCards workflows={workflows} onRun={(workflow) => void startExecution(workflow, true)} onReview={() => setTab("execution")} detailed /></section>}

      {tab === "execution" && <section className="screen"><div className="page-intro"><span className="eyebrow">EXECUTION MONITOR</span><h2>Action and verification status</h2><p>Attempted actions and verified checkpoints are tracked separately. A checkpoint advances only when the API confirms it.</p></div>{run && selectedWorkflow ? <article className="panel monitor-panel"><div className="monitor-head"><div><span className="eyebrow">{selectedWorkflow.name}</span><h3>Run {run.run.id}</h3><p>{run.message}</p></div><div className="monitor-actions"><button className="secondary" disabled={busy || !["executing", "waiting_for_confirmation"].includes(run.run.state)} onClick={() => void controlExecution("pause")}>Pause</button><button className="danger-outline" disabled={busy || ["completed", "failed"].includes(run.run.state)} onClick={() => void controlExecution("stop")}>Stop</button></div></div><div className="progress-summary"><div><small>LAST VERIFIED STEP</small><b>{verified} <i>/ {selectedWorkflow.steps.length || "?"}</i></b></div><div><small>RUN STATE</small><b>{titles[(run.run.state in titles ? run.run.state : "idle") as State]}</b></div><div><small>CURRENT ATTEMPT</small><b>{activeStep ? `Step ${activeStep.order}` : "Not reported"}</b></div></div><div className="timeline">{selectedWorkflow.steps?.map((step) => { const isVerified = step.order <= verified; const isFailed = run.verification?.step_id === step.id && !run.verification.passed; const isAttempted = activeStep?.id === step.id && !isVerified && !isFailed; return <div className={`timeline-step ${isVerified ? "verified" : isFailed ? "failed" : isAttempted ? "attempted" : "pending"}`} key={step.id}><span className="timeline-marker">{isVerified ? "✓" : isFailed ? "!" : isAttempted ? "…" : step.order}</span><div className="timeline-copy"><div><b>{step.action_type}</b><span className="status-tag">{isVerified ? "Verified" : isFailed ? "Failed verification" : isAttempted ? "Attempted · unverified" : "Not attempted"}</span></div><p>Expected: {shortValue(step.expected_result)}</p>{isVerified && <small>Checkpoint reported by API</small>}{isAttempted && <small>Action returned; verification not confirmed. Progress did not advance.</small>}{isFailed && <small>{run.verification?.message || "Expected result was not confirmed."}</small>}</div></div>; })}</div>{!selectedWorkflow.steps?.length && <Empty title="Step details unavailable" text="The workflow response did not include its ordered steps." />}</article> : <Empty title="No execution selected" text="Choose an approved workflow to start monitoring its attempted actions and verified checkpoints." action="Open workflow library" onClick={() => setTab("library")} />}</section>}
      <footer className="footer"><span>Echo.ai</span><span>Voice first · User approved · Progress verified</span><span>API status: {online === null ? "checking" : online ? "connected" : "offline"}</span></footer>
    </main>
  </div>;
}

function WorkflowCards({ workflows, onRun, onReview, detailed = false }: { workflows: Workflow[]; onRun: (workflow: Workflow) => void; onReview: () => void; detailed?: boolean }) {
  if (!workflows.length) return <Empty title="No approved workflows yet" text="Teach Echo a task, review its draft, and approve it before it appears in your library." action={detailed ? "Start teaching" : "Open library"} onClick={onReview} />;
  return <div className="workflow-grid">{workflows.map((workflow) => <article className="panel workflow-card" key={workflow.id}><div className="workflow-card-top"><span className="workflow-symbol">↗</span><span className="approved-badge"><i />Approved</span></div><h3>{workflow.name}</h3><p>{workflow.description || "No workflow description was provided."}</p><div className="workflow-facts"><span><b>{workflow.steps?.length ?? 0}</b> ordered steps</span><span><b>Last verified</b> Not reported by API</span></div>{detailed && <><div className="trigger-list"><b>Voice triggers</b><span>{workflow.trigger_phrases?.length ? workflow.trigger_phrases.join(" · ") : "No trigger phrases reported"}</span></div><div className="workflow-preview">{workflow.steps?.slice(0, 4).map((step) => <div key={step.id}><i>{step.order <= 0 ? "✓" : step.order}</i><span>{step.action_type} · {shortValue(step.target)}</span><small>Progress not reported</small></div>)}</div></>}<div className="workflow-actions"><button className="primary" onClick={() => onRun(workflow)}>Resume workflow <span>→</span></button><button className="icon-button" title="Open execution monitor" aria-label="Open execution monitor" onClick={onReview}>↗</button></div></article>)}</div>;
}

function Empty({ title, text, action, onClick }: { title: string; text: string; action?: string; onClick?: () => void }) {
  return <div className="panel empty"><span className="empty-symbol">◌</span><h3>{title}</h3><p>{text}</p>{action && onClick && <button className="secondary" onClick={onClick}>{action} →</button>}</div>;
}
