import React, { useEffect, useMemo, useState } from "https://esm.sh/react@18";
import { createRoot } from "https://esm.sh/react-dom@18/client";

const h = React.createElement;

function App() {
  const [tools, setTools] = useState([]);
  const [files, setFiles] = useState([]);
  const [executions, setExecutions] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [selectedTool, setSelectedTool] = useState("");
  const [toolParams, setToolParams] = useState({});
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const [agentInstruction, setAgentInstruction] = useState("");
  const [agentPreview, setAgentPreview] = useState(null);
  const [workflowPreview, setWorkflowPreview] = useState(null);
  const [workflowParams, setWorkflowParams] = useState({});
  const [activeWorkflowId, setActiveWorkflowId] = useState("");
  const [statusText, setStatusText] = useState("");

  useEffect(() => {
    refreshAll();
    const timer = window.setInterval(loadExecutions, 4000);
    return () => window.clearInterval(timer);
  }, []);

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }

  async function refreshAll() {
    await Promise.all([loadTools(), loadFiles(), loadExecutions(), loadWorkflows()]);
  }

  async function loadTools() {
    const data = await api("/api/tools", { headers: {} });
    setTools(data.tools || []);
  }

  async function loadFiles() {
    const data = await api("/api/files?page=1&limit=100", { headers: {} });
    setFiles(data.files || []);
  }

  async function loadExecutions() {
    const data = await api("/api/executions?limit=50", { headers: {} });
    setExecutions(data.executions || []);
  }

  async function loadWorkflows() {
    const data = await api("/api/workflows", { headers: {} });
    setWorkflows(data.workflows || []);
  }

  async function uploadFiles(fileList) {
    for (const file of fileList) {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/files", { method: "POST", body });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Upload failed");
      }
    }
    await loadFiles();
    setStatusText(`Uploaded ${fileList.length} file(s)`);
  }

  function toggleFileSelection(fileId) {
    setSelectedFileIds((current) =>
      current.includes(fileId) ? current.filter((item) => item !== fileId) : [...current, fileId]
    );
  }

  const currentTool = useMemo(() => tools.find((tool) => tool.name === selectedTool) || null, [tools, selectedTool]);
  const currentWorkflow = useMemo(
    () => workflows.find((workflow) => workflow.id === activeWorkflowId) || null,
    [workflows, activeWorkflowId]
  );

  async function createToolExecution() {
    if (!selectedTool || !currentTool) {
      setStatusText("Select a tool");
      return;
    }
    const minInputs = currentTool.inputs?.min ?? 1;
    const maxInputs = currentTool.inputs?.max ?? 1;

    if (selectedFileIds.length < minInputs) {
      setStatusText(`Tool requires at least ${minInputs} file(s)`);
      return;
    }

    if (maxInputs > 1) {
      if (selectedFileIds.length > maxInputs) {
        setStatusText(`Tool accepts at most ${maxInputs} file(s)`);
        return;
      }
      const execution = await api("/api/executions", {
        method: "POST",
        body: JSON.stringify({
          mode: "FORM",
          file_ids: [],
          steps: [
            {
              tool: selectedTool,
              params: toolParams,
              inputs: selectedFileIds.map((fileId) => ({ type: "file", file_id: fileId })),
            },
          ],
        }),
      });
      setStatusText(`Created execution ${execution.id}`);
    } else {
      const created = await Promise.all(
        selectedFileIds.map((fileId) =>
          api("/api/executions", {
            method: "POST",
            body: JSON.stringify({
              mode: "FORM",
              file_ids: [],
              steps: [{ tool: selectedTool, params: toolParams, inputs: [{ type: "file", file_id: fileId }] }],
            }),
          })
        )
      );
      setStatusText(
        created.length === 1 ? `Created execution ${created[0].id}` : `Created ${created.length} executions`
      );
    }
    await loadExecutions();
  }

  async function previewAgentPlan() {
    const preview = await api("/api/agent/plans/preview", {
      method: "POST",
      body: JSON.stringify({ message: agentInstruction, file_ids: selectedFileIds }),
    });
    setAgentPreview(preview);
    setStatusText("Plan preview ready");
  }

  async function confirmAgentPlan() {
    if (!agentPreview) {
      return;
    }
    const execution = await api("/api/agent/plans/confirm", {
      method: "POST",
      body: JSON.stringify({ mode: "AGENT", instruction: agentPreview.instruction, plan: agentPreview.plan }),
    });
    setAgentPreview(null);
    setStatusText(`Created agent execution ${execution.id}`);
    await loadExecutions();
  }

  async function previewWorkflow() {
    if (!activeWorkflowId) {
      setStatusText("Select a workflow");
      return;
    }
    const preview = await api(`/api/workflows/${activeWorkflowId}/plan`, {
      method: "POST",
      body: JSON.stringify({
        workflow_id: activeWorkflowId,
        file_ids: selectedFileIds,
        params: workflowParams,
        mode: "FORM",
      }),
    });
    setWorkflowPreview(preview);
    setStatusText("Workflow plan preview ready");
  }

  async function executeWorkflow() {
    if (!activeWorkflowId) {
      return;
    }
    const execution = await api(`/api/workflows/${activeWorkflowId}/execute`, {
      method: "POST",
      body: JSON.stringify({
        workflow_id: activeWorkflowId,
        file_ids: selectedFileIds,
        params: workflowParams,
        mode: "FORM",
      }),
    });
    setWorkflowPreview(null);
    setStatusText(`Created workflow execution ${execution.id}`);
    await loadExecutions();
  }

  async function cancelExecution(executionId) {
    await api(`/api/executions/${executionId}/cancel`, { method: "POST" });
    await loadExecutions();
  }

  return h(
    "div",
    { className: "app", style: { minHeight: "100vh" } },
    h(
      "aside",
      { className: "sidebar", style: { width: "360px", borderRight: "1px solid var(--border)" } },
      h("div", { className: "sidebar-header" }, h("h2", null, "PDF Toolbox"), h("div", { style: { fontSize: "12px", color: "var(--text-secondary)" } }, "React UI")) ,
      h("div", { style: { padding: "12px", display: "grid", gap: "12px" } },
        h("label", { className: "btn-new", style: { display: "block", textAlign: "center", cursor: "pointer" } },
          "Upload Files",
          h("input", {
            type: "file",
            multiple: true,
            style: { display: "none" },
            onChange: (event) => uploadFiles(Array.from(event.target.files || [])).catch((error) => setStatusText(error.message)),
          })
        ),
        h(Section, { title: "Files" },
          h(
            "div",
            { className: "file-manager", style: { maxHeight: "240px", overflow: "auto" } },
            files.map((file) =>
              h(
                "label",
                { key: file.id, className: "file-item", style: { cursor: "pointer" } },
                h("input", {
                  type: "checkbox",
                  checked: selectedFileIds.includes(file.id),
                  onChange: () => toggleFileSelection(file.id),
                  style: { marginRight: "8px" },
                }),
                h("div", { className: "fi-info" }, h("div", { className: "fi-name" }, file.orig_name), h("div", { className: "fi-meta" }, `${file.size_bytes} bytes`))
              )
            )
          )
        ),
        h(Section, { title: "Tool Form" },
          h(
            "select",
            {
              value: selectedTool,
              onChange: (event) => {
                setSelectedTool(event.target.value);
                setToolParams({});
              },
              style: inputStyle,
            },
            h("option", { value: "" }, "Select Tool"),
            tools.map((tool) => h("option", { key: tool.name, value: tool.name }, tool.label || tool.name))
          ),
          currentTool &&
            h(
              "div",
              { style: { display: "grid", gap: "8px", marginTop: "8px" } },
              currentTool.params.map((param) =>
                h(FieldInput, {
                  key: param.name,
                  param,
                  value: toolParams[param.name] ?? (param.default ?? ""),
                  onChange: (value) => setToolParams((current) => ({ ...current, [param.name]: value })),
                })
              )
            ),
          h("button", { className: "btn-send", style: buttonStyle, onClick: () => createToolExecution().catch((error) => setStatusText(error.message)) }, currentTool && (currentTool.inputs?.max ?? 1) === 1 && selectedFileIds.length > 1 ? "Create Executions" : "Create Execution")
        ),
        h(Section, { title: "Agent Plan Preview" },
          h("textarea", {
            value: agentInstruction,
            onChange: (event) => setAgentInstruction(event.target.value),
            placeholder: "Describe the PDF task in natural language",
            style: { ...inputStyle, minHeight: "96px" },
          }),
          h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
            h("button", { className: "btn-send", style: buttonStyle, onClick: () => previewAgentPlan().catch((error) => setStatusText(error.message)) }, "Preview Plan"),
            agentPreview && h("button", { className: "btn-send", style: buttonStyle, onClick: () => confirmAgentPlan().catch((error) => setStatusText(error.message)) }, "Confirm Execution")
          ),
          agentPreview && h(PlanView, { plan: agentPreview.plan })
        ),
        h(Section, { title: "Workflow Pipelines" },
          h(
            "select",
            {
              value: activeWorkflowId,
              onChange: (event) => {
                setActiveWorkflowId(event.target.value);
                setWorkflowParams({});
              },
              style: inputStyle,
            },
            h("option", { value: "" }, "Select Workflow"),
            workflows.map((workflow) => h("option", { key: workflow.id, value: workflow.id }, workflow.name))
          ),
          currentWorkflow &&
            h(
              "div",
              { style: { display: "grid", gap: "8px", marginTop: "8px" } },
              currentWorkflow.params.map((param) =>
                h(FieldInput, {
                  key: param.name,
                  param,
                  value: workflowParams[param.name] ?? (param.default ?? ""),
                  onChange: (value) => setWorkflowParams((current) => ({ ...current, [param.name]: value })),
                })
              )
            ),
          h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
            h("button", { className: "btn-send", style: buttonStyle, onClick: () => previewWorkflow().catch((error) => setStatusText(error.message)) }, "Preview Workflow"),
            currentWorkflow && h("button", { className: "btn-send", style: buttonStyle, onClick: () => executeWorkflow().catch((error) => setStatusText(error.message)) }, "Create Execution")
          ),
          workflowPreview && h(PlanView, { plan: workflowPreview.plan })
        ),
        h("div", { style: { fontSize: "12px", color: "var(--text-secondary)" } }, statusText)
      )
    ),
    h(
      "main",
      { className: "main", style: { padding: "24px" } },
      h("div", { className: "chat-header" }, h("h3", null, "Task Center"), h("span", { style: { fontSize: "12px", color: "var(--text-secondary)" } }, `${executions.length} executions`)),
      h(
        "div",
        { className: "messages", style: { padding: "0", background: "transparent" } },
        executions.length === 0
          ? h("div", { className: "empty-state", style: { marginTop: "32px" } }, h("h3", null, "No executions yet"), h("p", null, "Create an execution from a tool form, workflow, or agent plan preview."))
          : executions.map((execution) =>
              h(
                "div",
                { key: execution.id, className: "tool-card expanded", style: { marginBottom: "12px" } },
                h(
                  "div",
                  { className: "tc-header" },
                  h("div", { className: "tc-icon" }, "E"),
                  h("span", { className: "tc-name" }, execution.active_tool || (execution.logs && execution.logs[0] && execution.logs[0].tool) || execution.mode),
                  h("span", { className: "tc-status" }, `${execution.status} ${execution.progress_int}%`)
                ),
                h(
                  "div",
                  { className: "tc-body", style: { display: "block" } },
                  h("div", { className: "tc-output" }, `Execution ID: ${execution.id}`),
                  execution.error_message && h("div", { style: { fontSize: "12px", marginTop: "6px", color: "var(--danger)" } }, execution.error_message),
                  (execution.logs || []).map((step) =>
                    h("div", { key: `${execution.id}-${step.index}-${step.tool}`, style: { fontSize: "12px", marginTop: "6px" } }, `${step.index + 1}. ${step.tool} -> ${step.status}`)
                  ),
                  (execution.outputs || []).length > 0 &&
                    h(
                      "div",
                      { style: { display: "grid", gap: "6px", marginTop: "10px" } },
                      execution.outputs.map((output) =>
                        h(
                          "a",
                          { key: `${execution.id}-${output.filename}`, className: "wf-btn", href: output.download_url, download: true },
                          `Download ${output.filename}`
                        )
                      )
                    ),
                  h("div", { style: { display: "flex", gap: "8px", marginTop: "10px" } },
                    execution.result_path && h("a", { className: "wf-btn", href: `/api/executions/${execution.id}/result`, download: true }, "Download Result"),
                    ["PENDING", "RUNNING"].includes(execution.status) && h("button", { className: "wf-btn", onClick: () => cancelExecution(execution.id).catch((error) => setStatusText(error.message)) }, "Cancel")
                  )
                )
              )
            )
      )
    )
  );
}

function Section({ title, children }) {
  return h("section", { style: { display: "grid", gap: "8px" } }, h("h4", { style: { margin: 0, fontSize: "13px" } }, title), children);
}

function FieldInput({ param, value, onChange }) {
  if (param.options && param.options.length) {
    return h(
      "label",
      { style: { display: "grid", gap: "4px", fontSize: "12px" } },
      param.label,
      h(
        "select",
        { value, onChange: (event) => onChange(event.target.value), style: inputStyle },
        param.options.map((option) => h("option", { key: option, value: option }, option))
      )
    );
  }
  return h(
    "label",
    { style: { display: "grid", gap: "4px", fontSize: "12px" } },
    param.label,
    h("input", {
      value,
      onChange: (event) => onChange(event.target.value),
      placeholder: param.description || "",
      style: inputStyle,
    })
  );
}

function PlanView({ plan }) {
  return h(
    "div",
    { style: { marginTop: "10px", padding: "10px", border: "1px solid var(--border)", borderRadius: "10px", fontSize: "12px" } },
    h("div", { style: { marginBottom: "6px", color: "var(--text-secondary)" } }, "Plan Preview"),
    plan.steps.map((step, index) =>
      h("div", { key: `${step.tool}-${index}`, style: { marginTop: "6px" } }, `${index + 1}. ${step.tool} ${JSON.stringify(step.params || {})}`)
    )
  );
}

const inputStyle = {
  width: "100%",
  border: "1px solid var(--border)",
  borderRadius: "8px",
  padding: "8px 10px",
  background: "var(--bg-input)",
  color: "var(--text)",
};

const buttonStyle = {
  border: "none",
  borderRadius: "8px",
  padding: "8px 12px",
  cursor: "pointer",
};

createRoot(document.getElementById("root")).render(h(App));
