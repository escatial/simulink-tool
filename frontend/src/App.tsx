import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { graphlib, layout as dagreLayout } from "@dagrejs/dagre";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { api, Document, Job, Model, ModelSummary, Project } from "./api";

const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  draft: "待确认",
  confirmed: "已确认",
  "可直接搭建": "可直接搭建",
  "补充参数后可搭建": "补充参数后可搭建",
  "结构存在歧义": "结构存在歧义",
  "仅供参考": "仅供参考",
};

type BlockNodeData = { id: string; name: string; kind: string; description: string };
type BlockNodeType = Node<BlockNodeData, "simulinkBlock">;

function SimulinkBlock({ data }: NodeProps<BlockNodeType>) {
  return <div className="simulink-node">
    <Handle type="target" position={Position.Left} />
    <div className="simulink-node-meta"><span>{data.id}</span><span>{data.kind}</span></div>
    <strong>{data.name}</strong>
    <small>{data.description || "Simulink 模块"}</small>
    <Handle type="source" position={Position.Right} />
  </div>;
}

const graphNodeTypes = { simulinkBlock: SimulinkBlock };

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [documents, setDocuments] = useState<Document[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [projectForm, setProjectForm] = useState({ name: "", description: "" });

  const selectedProject = useMemo(() => projects.find((project) => project.id === projectId), [projects, projectId]);

  useEffect(() => {
    api.projects().then(setProjects).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([api.project(projectId), api.models(projectId)])
      .then(([project, projectModels]) => {
        setDocuments(project.documents);
        setModels(projectModels);
      })
      .catch((err) => setError(err.message));
  }, [projectId, job?.status]);

  useEffect(() => {
    if (!job || ["completed", "failed"].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.job(job.id);
        setJob(latest);
        if (latest.status === "completed") {
          setNotice("文档解析和建模方案生成完成");
          window.clearInterval(timer);
        }
        if (latest.status === "failed") {
          setError(latest.error_message || "处理失败");
          window.clearInterval(timer);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "任务状态查询失败");
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job]);

  async function createProject() {
    if (!projectForm.name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const project = await api.createProject(projectForm);
      setProjects((current) => [project, ...current]);
      setProjectId(project.id);
      setProjectForm({ name: "", description: "" });
      setNotice("项目已创建");
    } catch (err) {
      setError(err instanceof Error ? err.message : "项目创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !projectId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.upload(projectId, file);
      setDocuments((current) => [result.document, ...current]);
      setJob(result.job);
      setNotice("文件已上传，正在生成方案");
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function openModel(summary: ModelSummary) {
    setError("");
    try {
      setSelectedModel(await api.model(summary.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "方案读取失败");
    }
  }

  async function confirmModel() {
    if (!selectedModel) return;
    setBusy(true);
    try {
      const hasErrors = selectedModel.model.validation.issues.some((issue) => issue.severity === "error");
      if (hasErrors && !window.confirm("当前方案仍有阻断问题，确认后将保留这些问题并进入已确认版本。是否继续？")) {
        return;
      }
      const confirmed = await api.confirm(selectedModel.id, hasErrors);
      setSelectedModel(confirmed);
      setModels((current) => [confirmed, ...current]);
      setNotice("方案已确认，可以导出");
    } catch (err) {
      setError(err instanceof Error ? err.message : "方案确认失败");
    } finally {
      setBusy(false);
    }
  }

  async function exportModel() {
    if (!selectedModel) return;
    setBusy(true);
    try {
      const result = await api.export(selectedModel.id);
      setNotice("方案包已生成");
      const zip = result.artifacts.find((artifact) => artifact.filename.endsWith(".zip"));
      if (zip) window.open(zip.download_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">DOCUMENT-DRIVEN MODELING</p>
          <h1>Simulink 建模助手</h1>
        </div>
        <div className="topbar-status"><span className="status-dot" />本地工作台</div>
      </header>
      <main className="workspace">
        <aside className="sidebar">
          <section className="panel project-panel">
            <div className="panel-heading"><span>项目</span><span className="count">{projects.length}</span></div>
            <div className="project-list">
              {projects.map((project) => (
                <button key={project.id} className={`project-item ${projectId === project.id ? "active" : ""}`} onClick={() => { setProjectId(project.id); setSelectedModel(null); }}>
                  <strong>{project.name}</strong><span>{project.document_count} 份文档</span>
                </button>
              ))}
              {!projects.length && <p className="empty-copy">创建第一个项目开始。</p>}
            </div>
          </section>
          <section className="panel create-panel">
            <div className="panel-heading"><span>新建项目</span></div>
            <input value={projectForm.name} onChange={(event) => setProjectForm({ ...projectForm, name: event.target.value })} placeholder="项目名称" />
            <textarea value={projectForm.description} onChange={(event) => setProjectForm({ ...projectForm, description: event.target.value })} placeholder="项目说明（可选）" rows={3} />
            <button className="primary-button" disabled={busy || !projectForm.name.trim()} onClick={createProject}>创建项目</button>
          </section>
        </aside>

        <section className="content">
          {!selectedProject ? (
            <div className="empty-state"><div className="empty-mark">∿</div><h2>从一份需求文档开始</h2><p>系统会提取控制目标、模块、参数和连接关系，生成可检查的 Simulink 建模方案。</p></div>
          ) : (
            <>
              <div className="content-header">
                <div><p className="eyebrow">PROJECT / {selectedProject.name}</p><h2>{selectedProject.name}</h2><p className="muted">上传 DOCX、PDF 或 Markdown，解析结果会保留原文证据。</p></div>
                <label className={`upload-button ${busy ? "disabled" : ""}`}><span>＋</span>上传需求文档<input type="file" accept=".pdf,.docx,.md,.markdown,.txt" onChange={upload} disabled={busy} /></label>
              </div>
              {(notice || error) && <div className={`notice ${error ? "error" : ""}`}>{error || notice}</div>}
              {job && <div className="job-card"><div><strong>解析任务</strong><span>{statusLabel[job.status] || job.status} · {job.stage}</span></div><div className="progress"><div style={{ width: `${job.progress}%` }} /></div><b>{job.progress}%</b></div>}
              <div className="dashboard-grid">
                <section className="panel data-panel"><div className="panel-heading"><span>文档</span><span className="count">{documents.length}</span></div>{documents.length ? documents.map((document) => <div className="document-row" key={document.id}><div className="file-icon">{document.extension.replace(".", "").toUpperCase()}</div><div><strong>{document.filename}</strong><span>{statusLabel[document.parse_status] || document.parse_status}</span></div></div>) : <p className="empty-copy">还没有上传文档。</p>}</section>
                <section className="panel data-panel"><div className="panel-heading"><span>方案版本</span><span className="count">{models.length}</span></div>{models.length ? models.map((model) => <button className="model-row" key={model.id} onClick={() => openModel(model)}><div className="version">v{model.version_no}</div><div><strong>{model.system_name}</strong><span>{statusLabel[model.state] || model.state} · {statusLabel[model.buildability_status] || model.buildability_status}</span></div><span className="arrow">›</span></button>) : <p className="empty-copy">解析完成后会出现方案版本。</p>}</section>
              </div>
              {selectedModel && <ModelDetail model={selectedModel} busy={busy} onConfirm={confirmModel} onExport={exportModel} />}
            </>
          )}
        </section>
      </main>
    </div>
  );
}

function ModelDetail({ model, busy, onConfirm, onExport }: { model: Model; busy: boolean; onConfirm: () => void; onExport: () => void }) {
  const { modules, parameters, connections, validation, open_questions } = model.model;
  return <section className="model-detail panel">
    <div className="detail-header"><div><p className="eyebrow">MODEL VERSION / V{model.version_no}</p><h2>{model.model.system_name}</h2><div className="badges"><span className={`badge ${model.state === "confirmed" ? "success" : "warning"}`}>{statusLabel[model.state] || model.state}</span><span className="badge neutral">{statusLabel[model.buildability_status] || model.buildability_status}</span></div></div><div className="detail-actions"><button className="secondary-button" disabled={busy || model.state !== "draft"} onClick={onConfirm}>确认方案</button><button className="primary-button" disabled={busy || model.state !== "confirmed"} onClick={onExport}>导出方案包</button></div></div>
    <div className="model-summary"><div><span>领域</span><strong>{model.model.domain}</strong></div><div><span>控制目标</span><strong>{model.model.control_objective || "待确认"}</strong></div><div><span>校验</span><strong>{validation.issues.length ? `${validation.issues.length} 项问题` : "通过"}</strong></div></div>
    <ModelGraph model={model.model} />
    {validation.issues.length > 0 && <div className="issues"><h3>校验与待确认项</h3>{validation.issues.map((issue, index) => <div className={`issue ${issue.severity}`} key={`${issue.code}-${index}`}><span>{issue.severity === "error" ? "!" : "i"}</span>{issue.message}</div>)}{open_questions.map((question, index) => <div className="issue warning" key={`question-${index}`}><span>?</span>{question.question}</div>)}</div>}
    <div className="tables"><DataTable title="模块清单" headers={["编号", "模块", "Simulink 类型", "说明"]} rows={modules.map((item) => [item.id, item.name, item.simulink_type, item.description])} /><DataTable title="参数表" headers={["模块", "参数", "值", "单位", "状态"]} rows={parameters.map((item) => [item.owner_id || "-", item.name, item.value ?? "未提供", item.unit || "未知", item.status])} /><DataTable title="连线关系" headers={["起点", "终点", "信号", "含义"]} rows={connections.map((item) => [`${item.source_module}/${item.source_port}`, `${item.target_module}/${item.target_port}`, item.signal_name, item.meaning])} /></div>
  </section>;
}

function ModelGraph({ model }: { model: Model["model"] }) {
  const { nodes, edges } = useMemo(() => {
    const layout = new graphlib.Graph();
    layout.setGraph({ rankdir: "LR", ranksep: 110, nodesep: 54, marginx: 40, marginy: 36 });
    layout.setDefaultEdgeLabel(() => ({}));
    for (const module of model.modules) layout.setNode(module.id, { width: 196, height: 92 });
    for (const connection of model.connections) {
      if (layout.hasNode(connection.source_module) && layout.hasNode(connection.target_module)) {
        layout.setEdge(connection.source_module, connection.target_module);
      }
    }
    dagreLayout(layout);
    const nodes: BlockNodeType[] = model.modules.map((module) => {
      const point = layout.node(module.id) || { x: 100, y: 100 };
      return {
        id: module.id,
        type: "simulinkBlock",
        position: { x: point.x - 98, y: point.y - 46 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: { id: module.id, name: module.name, kind: module.simulink_type, description: module.description },
      };
    });
    const edges: Edge[] = model.connections
      .filter((connection) => model.modules.some((module) => module.id === connection.source_module) && model.modules.some((module) => module.id === connection.target_module))
      .map((connection, index) => ({
        id: `edge-${index}`,
        source: connection.source_module,
        target: connection.target_module,
        label: connection.signal_name,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#54708f" },
        style: { stroke: "#54708f", strokeWidth: 1.8 },
        labelStyle: { fill: "#60748d", fontSize: 11 },
        labelBgStyle: { fill: "#fff", fillOpacity: 0.92 },
      }));
    return { nodes, edges };
  }, [model]);

  return <section className="graph-section">
    <div className="table-heading"><h3>Simulink 结构图</h3><span>从左向右 · 可缩放和拖动画布</span></div>
    <div className="graph-canvas">
      {nodes.length ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={graphNodeTypes} fitView fitViewOptions={{ padding: 0.16 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable proOptions={{ hideAttribution: true }}>
        <Background color="#dfe6ef" gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow> : <p className="empty-copy">尚未识别出模块。</p>}
    </div>
  </section>;
}

function DataTable({ title, headers, rows }: { title: string; headers: string[]; rows: Array<Array<string | number | null>> }) {
  return <div className="table-block"><div className="table-heading"><h3>{title}</h3><span>{rows.length} 项</span></div>{rows.length ? <div className="table-scroll"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? "-"}</td>)}</tr>)}</tbody></table></div> : <p className="empty-copy">暂无数据。</p>}</div>;
}

export default App;
