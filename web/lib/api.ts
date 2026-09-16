import {
  Job,
  PipelineMetrics,
  MemberItem,
  LocatorItem,
  TopologyJoint,
  CandidateItem,
  ShopDrawingItem,
  BOMItem,
  RegressionDrawingScore,
  ReviewItem
} from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch(`${API_BASE}/api/jobs`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch jobs');
  return res.json();
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch job ${jobId}`);
  return res.json();
}

export async function createJob(formData: FormData): Promise<Job> {
  const res = await fetch(`${API_BASE}/api/jobs`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(err.detail || 'Failed to create job');
  }
  return res.json();
}

export async function runJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/run`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to trigger job run');
  return res.json();
}

export async function deleteJob(jobId: string): Promise<{ status: string; job_id: string }> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete job' }));
    throw new Error(err.detail || 'Failed to delete job');
  }
  return res.json();
}

export async function fetchJobLogs(jobId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/logs`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch job logs');
  const data = await res.json();
  return data.logs || '';
}

export async function fetchMetrics(jobId: string): Promise<PipelineMetrics> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/metrics`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch metrics');
  return res.json();
}

export async function fetchMembers(jobId: string): Promise<MemberItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/members`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch members');
  return res.json();
}

export async function fetchLocators(jobId: string): Promise<LocatorItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/locators`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch locators');
  return res.json();
}

export async function fetchTopology(jobId: string): Promise<TopologyJoint[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/topology`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch topology');
  return res.json();
}

export async function fetchInference(jobId: string): Promise<CandidateItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/inference`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch candidates');
  return res.json();
}

export async function fetchDrawings(jobId: string): Promise<ShopDrawingItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/drawings`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch drawings');
  return res.json();
}

export async function fetchDrawingDetail(jobId: string, backmark: string): Promise<any> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/drawings/${backmark}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch drawing detail for ${backmark}`);
  return res.json();
}

export async function fetchBOM(jobId: string): Promise<BOMItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/bom`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch BOM');
  return res.json();
}

export async function fetchRegression(jobId: string): Promise<RegressionDrawingScore[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/regression`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch regression scores');
  return res.json();
}

export async function fetchReview(jobId: string): Promise<ReviewItem[]> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/review`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch review queue');
  return res.json();
}

export function getArtifactUrl(jobId: string, path: string): string {
  return `${API_BASE}/api/jobs/${jobId}/artifacts/${path}`;
}

export function getEventsUrl(jobId: string): string {
  return `${API_BASE}/api/jobs/${jobId}/events`;
}
