'use client';

import Link from 'next/link';
import { Layers, Plus, Cpu, Activity } from 'lucide-react';

export function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-surface/80 backdrop-blur-md px-6 py-3.5 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <Link href="/jobs" className="flex items-center gap-3 group">
          <div className="w-8 h-8 rounded bg-brand-600 flex items-center justify-center text-white shadow-md shadow-brand-600/20 group-hover:bg-brand-500 transition-colors">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-sm tracking-wider uppercase text-slate-100">Antigravity Core</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-brand-500/10 text-brand-500 border border-brand-500/20">PROD</span>
            </div>
            <p className="text-[11px] text-slate-400 font-mono">Transmission Tower Engineering Detailing</p>
          </div>
        </Link>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-3 py-1 rounded bg-slate-900/60 border border-slate-800 text-[11px] font-mono text-slate-400">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          Python Engine: Online
        </div>

        <Link
          href="/jobs/new"
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-sm transition-all"
        >
          <Plus className="w-4 h-4" />
          New DXF Job
        </Link>
      </div>
    </header>
  );
}
