export type Project = {
  id: string;
  name: string;
  description: string;
  created_at: string;
  document_count: number;
};

export type Document = {
  id: string;
  filename: string;
  extension: string;
  size_bytes: number;
  parse_status: string;
  created_at: string;
};

export type Job = {
  id: string;
  status: string;
  stage: string;
  progress: number;
  error_message?: string | null;
};

export type ModelSummary = {
  id: string;
  document_id: string;
  version_no: number;
  state: string;
  buildability_status: string;
  system_name: string;
  created_at: string;
};

export type Model = ModelSummary & {
  model: {
    system_name: string;
    domain: string;
    control_objective: string;
    modules: Array<{ id: string; name: string; simulink_type: string; description: string; evidence: string[] }>;
    parameters: Array<{ id: string; owner_id?: string; name: string; value: string | number | null; unit: string; status: string; evidence: string[] }>;
    connections: Array<{ source_module: string; source_port: string; target_module: string; target_port: string; signal_name: string; meaning: string; evidence: string[] }>;
    validation: { status: string; issues: Array<{ severity: string; code: string; message: string }> };
    open_questions: Array<{ question: string; severity: string }>;
    buildability_status: string;
  };
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(body?.detail || `请求失败（${response.status}）`);
  }
  return body as T;
}

export const api = {
  projects: () => request<Project[]>("/api/v1/projects"),
  createProject: (payload: { name: string; description: string }) =>
    request<Project>("/api/v1/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  project: (id: string) => request<{ documents: Document[] }>(`/api/v1/projects/${id}`),
  upload: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ document: Document; job: Job }>(`/api/v1/projects/${projectId}/documents`, { method: "POST", body: form });
  },
  job: (id: string) => request<Job>(`/api/v1/jobs/${id}`),
  models: (projectId: string) => request<ModelSummary[]>(`/api/v1/projects/${projectId}/models`),
  model: (id: string) => request<Model>(`/api/v1/models/${id}`),
  confirm: (id: string, acknowledge_issues: boolean) => request<Model>(`/api/v1/models/${id}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ acknowledge_issues }) }),
  export: (id: string) => request<{ artifacts: Array<{ id: string; filename: string; download_url: string }> }>(`/api/v1/models/${id}/exports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_version_id: id, formats: ["svg", "csv", "md", "json"] }) }),
};
