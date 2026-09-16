import React from 'react';
import { cn } from '@/lib/utils';

interface StatusBadgeProps {
  status: string;
  className?: string;
  size?: 'sm' | 'md';
}

export function StatusBadge({ status, className, size = 'md' }: StatusBadgeProps) {
  const norm = (status || '').toUpperCase().trim();

  let colorClasses = 'bg-slate-800 text-slate-300 border-slate-700';

  if (['PASS', 'BUILDABLE', 'VALIDATED', 'AUTO', 'COMPLETED', 'FABRICATION READY'].includes(norm)) {
    colorClasses = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
  } else if (['FAIL', 'FAILED', 'REJECT', 'CANCELLED'].includes(norm)) {
    colorClasses = 'bg-rose-500/10 text-rose-400 border-rose-500/30';
  } else if (['BLOCKED', 'MISSING_FABRICATION_HOLES'].includes(norm)) {
    colorClasses = 'bg-amber-500/10 text-amber-400 border-amber-500/30';
  } else if (['REVIEW', 'REVIEW_REQUIRED', 'CANDIDATE'].includes(norm)) {
    colorClasses = 'bg-sky-500/10 text-sky-400 border-sky-500/30';
  } else if (['RUNNING', 'QUEUED'].includes(norm)) {
    colorClasses = 'bg-brand-500/10 text-brand-400 border-brand-500/30 animate-pulse';
  }

  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs';

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 font-mono font-medium rounded border tracking-wider uppercase',
        colorClasses,
        sizeClasses,
        className
      )}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current opacity-75"></span>
      {status}
    </span>
  );
}
