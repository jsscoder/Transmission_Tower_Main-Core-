import React from 'react';
import { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface MetricsCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: LucideIcon;
  variant?: 'default' | 'success' | 'warning' | 'info' | 'danger';
  className?: string;
}

export function MetricsCard({
  label,
  value,
  subtext,
  icon: Icon,
  variant = 'default',
  className,
}: MetricsCardProps) {
  const borderVariants = {
    default: 'border-border',
    success: 'border-emerald-500/30 bg-emerald-500/5',
    warning: 'border-amber-500/30 bg-amber-500/5',
    info: 'border-sky-500/30 bg-sky-500/5',
    danger: 'border-rose-500/30 bg-rose-500/5',
  };

  const textVariants = {
    default: 'text-slate-100',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    info: 'text-sky-400',
    danger: 'text-rose-400',
  };

  return (
    <div
      className={cn(
        'rounded-lg border bg-surface p-4 transition-all hover:border-slate-600',
        borderVariants[variant],
        className
      )}
    >
      <div className="flex items-center justify-between text-slate-400 mb-2">
        <span className="text-xs font-mono uppercase tracking-wider">{label}</span>
        {Icon && <Icon className="w-4 h-4 opacity-70" />}
      </div>
      <div className={cn('text-2xl font-bold font-mono', textVariants[variant])}>
        {value}
      </div>
      {subtext && (
        <p className="text-[11px] text-slate-400 mt-1 font-mono">{subtext}</p>
      )}
    </div>
  );
}
