'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Box,
  Crosshair,
  GitFork,
  CheckCircle2,
  FileSpreadsheet,
  Award,
  AlertTriangle,
  FolderTree,
  ArrowLeft
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface JobSidebarProps {
  jobId: string;
}

export function JobSidebar({ jobId }: JobSidebarProps) {
  const pathname = usePathname();

  const links = [
    { href: `/jobs/${jobId}`, label: 'Overview & Status', icon: LayoutDashboard },
    { href: `/jobs/${jobId}/members`, label: 'Members Schedule', icon: Box },
    { href: `/jobs/${jobId}/locators`, label: 'Locators & Callouts', icon: Crosshair },
    { href: `/jobs/${jobId}/topology`, label: 'Joint Topology', icon: GitFork },
    { href: `/jobs/${jobId}/inference`, label: 'Connection Inference', icon: CheckCircle2 },
    { href: `/jobs/${jobId}/drawings`, label: 'CAD Shop Drawings', icon: FolderTree },
    { href: `/jobs/${jobId}/bom`, label: 'Bill of Materials', icon: FileSpreadsheet },
    { href: `/jobs/${jobId}/regression`, label: 'Golden Regression (4)', icon: Award },
    { href: `/jobs/${jobId}/review`, label: 'Review Queue', icon: AlertTriangle },
  ];

  return (
    <aside className="w-64 border-r border-border bg-surface flex flex-col shrink-0 min-h-[calc(100vh-57px)]">
      <div className="p-4 border-b border-border">
        <Link
          href="/jobs"
          className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors font-mono mb-3"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to all jobs
        </Link>
        <div className="text-[11px] font-mono text-slate-500 uppercase tracking-wider">Active Analysis Job</div>
        <div className="font-mono text-xs text-slate-200 font-bold truncate mt-0.5" title={jobId}>
          {jobId}
        </div>
      </div>

      <nav className="p-2 space-y-1 flex-1">
        {links.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                'flex items-center gap-2.5 px-3 py-2 rounded text-xs font-medium transition-colors font-mono',
                isActive
                  ? 'bg-brand-500/10 text-brand-400 border border-brand-500/20'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              )}
            >
              <Icon className={cn('w-4 h-4 shrink-0', isActive ? 'text-brand-400' : 'text-slate-400')} />
              <span>{link.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
