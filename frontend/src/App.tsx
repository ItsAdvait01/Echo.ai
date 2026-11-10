/** Frontend starter. A4 exclusively owns frontend/**. */
import { useState } from "react";

type AgentState =
  | "Idle"
  | "Listening"
  | "Learning"
  | "Processing"
  | "Awaiting approval"
  | "Executing"
  | "Paused"
  | "Completed"
  | "Failed";

const steps = [
  "Open course dashboard",
  "Find the next incomplete lesson",
  "Open lesson and verify progress",
];

export default function App() {
  const [state, setState] = useState<AgentState>("Idle");

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand-mark">e</div>
        <div>
          <p className="eyebrow">PERSONAL WORKFLOW AGENT</p>
          <h1>Echo.ai</h1>
        </div>
        <div className="status-pill"><span className="status-dot" />{state}</div>
      </header>

      <section className="hero panel">
        <div>
          <p className="eyebrow">YOUR WORKFLOWS, READY WHEN YOU ARE</p>
          <h2>Teach once.<br /><span>Pick up anytime.</span></h2>
          <p className="muted">Show Echo how you work. Review what it learns. Ask it to continue when you are ready.</p>
          <div className="command-row">
            <button className="mic-button" aria-label="Start voice command" onClick={() => setState("Listening")}>●</button>
            <input aria-label="Voice command text fallback" placeholder="Try: Continue my Python course" />
            <button className="send-button" onClick={() => setState("Processing")}>Send</button>
          </div>
          <p className="fine-print">Voice availability depends on the configured speech service. Text input is the fallback.</p>
        </div>
        <div className="orb" aria-hidden="true"><div className="orb-core" /></div>
      </section>

      <section className="section-heading">
        <div><p className="eyebrow">START HERE</p><h2>What would you like to do?</h2></div>
      </section>
      <section className="action-grid">
        <article className="panel action-card">
          <div className="card-icon teach-icon">↗</div><p className="eyebrow">TEACH MODE</p>
          <h3>Show Echo a task</h3><p className="muted">Demonstrate a browser workflow in a session you control.</p>
          <button className="secondary-button" onClick={() => setState("Learning")}>Start teaching</button>
        </article>
        <article className="panel action-card">
          <div className="card-icon execute-icon">▶</div><p className="eyebrow">EXECUTE MODE</p>
          <h3>Resume a workflow</h3><p className="muted">Ask Echo to find the next step and verify its progress.</p>
          <button className="secondary-button" onClick={() => setState("Executing")}>Choose workflow</button>
        </article>
      </section>

      <section className="section-heading library-heading">
        <div><p className="eyebrow">SAVED MEMORY</p><h2>Your workflows</h2></div><button className="text-button">View library <span>→</span></button>
      </section>
      <section className="panel workflow-card">
        <div className="workflow-symbol">Py</div>
        <div className="workflow-copy"><h3>Continue Python course</h3><p className="muted">Last verified: Lesson 3 <span className="separator">·</span> 9 lessons remaining</p></div>
        <div className="workflow-status"><span className="verified-dot" />Approved</div>
        <button className="run-button" onClick={() => setState("Executing")}>Resume <span>→</span></button>
      </section>

      <section className="panel checkpoint-panel">
        <div><p className="eyebrow">NEXT CHECKPOINT</p><h3>Lesson 4</h3><p className="muted">Echo will verify the lesson page before updating progress.</p></div>
        <ol>{steps.map((step, index) => <li key={step}><span className={index < 1 ? "step-check done" : "step-check"}>{index < 1 ? "✓" : index + 1}</span>{step}</li>)}</ol>
      </section>

      <footer><span>Echo.ai</span><span>Teach once. Remember intelligently. Execute through voice.</span></footer>
    </main>
  );
}
