'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Terminal, X, RefreshCw, Copy, Check, Download, Maximize2, Minimize2 } from 'lucide-react';
import { fetchJobLogs, getArtifactUrl } from '@/lib/api';

interface LogTerminalModalProps {
  isOpen: boolean;
  onClose: () => void;
  jobId: string;
  isRunning?: boolean;
}

export function LogTerminalModal({ isOpen, onClose, jobId, isRunning = false }: LogTerminalModalProps) {
  const [logs, setLogs] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [isMaximized, setIsMaximized] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  const loadLogs = async () => {
    try {
      setLoading(true);
      const data = await fetchJobLogs(jobId);
      setLogs(data);
    } catch (err) {
      console.error('Failed to load logs', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    loadLogs();

    let interval: NodeJS.Timeout | null = null;
    if (isRunning) {
      interval = setInterval(loadLogs, 1500);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isOpen, jobId, isRunning]);

  useEffect(() => {
    if (autoScroll && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const handleCopy = () => {
    navigator.clipboard.writeText(logs);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isOpen) return null;

  // Format log lines with color highlighting
  const renderLogLines = () => {
    if (!logs) {
      return <div className="text-slate-500 italic">No logs recorded yet. Waiting for engine output...</div>;
    }

    const lines = logs.split('\n');
    return lines.map((line, idx) => {
      let color = 'text-slate-300';
      if (line.includes('=== TRANSMISSION TOWER')) color = 'text-brand-400 font-bold';
      else if (line.includes('=== PROCESS TERMINATED')) color = 'text-emerald-400 font-bold';
      else if (line.includes('ERROR') || line.includes('CRITICAL') || line.includes('FAILED')) color = 'text-rose-400 font-bold';
      else if (line.includes('WARNING')) color = 'text-amber-400';
      else if (line.includes('[1/7]') || line.includes('[2/7]') || line.includes('[3/7]') || line.includes('[4/7]') || line.includes('[5/7]') || line.includes('[6/7]') || line.includes('[7/7]') || line.includes('[DONE]')) {
        color = 'text-cyan-300 font-bold';
      } else if (line.includes('INFO')) {
        color = 'text-slate-300';
      }

      return (
        <div key={idx} className={`${color} leading-relaxed hover:bg-slate-900/60 px-1 rounded`}>
          <span className="text-slate-600 select-none text-[10px] w-8 inline-block text-right mr-3">
            {idx + 1}
          </span>
          {line}
        </div>
      );
    });
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div
        className={`w-full bg-[#0d1117] border border-border rounded-lg shadow-2xl flex flex-col font-mono text-xs overflow-hidden transition-all duration-200 ${
          isMaximized ? 'h-[95vh] max-w-[95vw]' : 'h-[650px] max-w-4xl'
        }`}
      >
        {/* Terminal Title Bar */}
        <div className="bg-[#161b22] px-4 py-2.5 border-b border-border flex items-center justify-between select-none">
          <div className="flex items-center gap-3">
            {/* Window control dots */}
            <div className="flex items-center gap-1.5">
              <button
                onClick={onClose}
                className="w-3 h-3 rounded-full bg-rose-500/80 hover:bg-rose-500 transition-colors"
                title="Close"
              />
              <button
                onClick={() => setIsMaximized(!isMaximized)}
                className="w-3 h-3 rounded-full bg-amber-500/80 hover:bg-amber-500 transition-colors"
                title="Toggle Maximize"
              />
              <button
                onClick={loadLogs}
                className="w-3 h-3 rounded-full bg-emerald-500/80 hover:bg-emerald-500 transition-colors"
                title="Refresh"
              />
            </div>
            <div className="flex items-center gap-2 text-slate-300 text-xs font-semibold">
              <Terminal className="w-4 h-4 text-brand-400" />
              <span>terminal@pipeline:{jobId}/engine.log</span>
              {isRunning && (
                <span className="flex items-center gap-1 text-[10px] text-brand-400 bg-brand-500/10 px-2 py-0.5 rounded border border-brand-500/20 animate-pulse">
                  <span className="w-1.5 h-1.5 rounded-full bg-brand-400"></span>
                  LIVE STREAM
                </span>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              className={`px-2 py-1 rounded text-[11px] transition-colors border ${
                autoScroll
                  ? 'bg-brand-500/10 text-brand-400 border-brand-500/30'
                  : 'bg-slate-800 text-slate-400 border-slate-700'
              }`}
            >
              Auto-scroll: {autoScroll ? 'ON' : 'OFF'}
            </button>
            <button
              onClick={handleCopy}
              className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              title="Copy to clipboard"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={loadLogs}
              className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              title="Refresh logs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setIsMaximized(!isMaximized)}
              className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              title={isMaximized ? 'Restore' : 'Maximize'}
            >
              {isMaximized ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded bg-slate-800 hover:bg-rose-500 hover:text-white text-slate-300 transition-colors"
              title="Close window"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Terminal Screen Body */}
        <div className="flex-1 p-4 overflow-y-auto font-mono text-[11px] bg-[#090d16] select-text">
          {renderLogLines()}
          <div ref={terminalEndRef} />
        </div>

        {/* Terminal Status Bar */}
        <div className="bg-[#161b22] px-4 py-1.5 border-t border-border flex items-center justify-between text-[10px] text-slate-500 select-none">
          <span>UTF-8 | LF | Engine Subprocess Console</span>
          <span>{logs.split('\n').length} lines captured</span>
        </div>
      </div>
    </div>
  );
}
