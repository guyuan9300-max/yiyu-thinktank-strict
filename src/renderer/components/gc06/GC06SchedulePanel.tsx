import React, { useEffect, useState } from 'react';
import { CalendarDays, LoaderCircle, Users } from 'lucide-react';

import { gc06Api } from './gc06Api';
import type { GC06CalendarEntry, GC06EventLine, GC06Meeting } from './gc06Contract';

type Flash = (level: 'success' | 'error', message: string) => void;

export function GC06SchedulePanel({
  clientId,
  flash,
}: {
  clientId: string | null;
  flash: Flash;
}) {
  const [meetings, setMeetings] = useState<GC06Meeting[]>([]);
  const [calendar, setCalendar] = useState<GC06CalendarEntry[]>([]);
  const [eventLines, setEventLines] = useState<GC06EventLine[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState('');
  const [eventLineId, setEventLineId] = useState('');
  const [startsAt, setStartsAt] = useState('');
  const [endsAt, setEndsAt] = useState('');

  const refresh = async () => {
    setLoading(true);
    try {
      const [nextMeetings, nextCalendar, nextEventLines] = await Promise.all([
        gc06Api.listMeetings(clientId || undefined),
        gc06Api.listCalendar(),
        gc06Api.listEventLines(clientId || undefined),
      ]);
      setMeetings(nextMeetings);
      setCalendar(nextCalendar);
      setEventLines(nextEventLines);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '会议与日历加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [clientId]);

  const createMeeting = async () => {
    if (!clientId || !title.trim() || !startsAt || !endsAt) return;
    try {
      setBusy(true);
      await gc06Api.createMeeting({
        clientId,
        eventLineId: eventLineId || null,
        title: title.trim(),
        startsAt,
        endsAt,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
      setTitle('');
      setStartsAt('');
      setEndsAt('');
      flash('success', '会议已创建，日历投影已派生');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '会议创建失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4" data-gc06-schedule-panel>
      <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Users className="h-4 w-4 text-indigo-600" />客户会议</div>
        <div className="mt-3 grid gap-2 md:grid-cols-[1fr_10rem_12rem_12rem_auto]">
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={clientId ? '会议标题' : '先选择客户'} disabled={!clientId || busy} className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <select value={eventLineId} onChange={(event) => setEventLineId(event.target.value)} disabled={!clientId || busy} className="rounded-xl border border-slate-200 px-3 py-2 text-sm"><option value="">不挂事件线</option>{eventLines.filter((line) => line.clientId === clientId && line.lifecycleState === 'active').map((line) => <option key={line.id} value={line.id}>{line.name}</option>)}</select>
          <input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <button type="button" onClick={() => void createMeeting()} disabled={!clientId || !title.trim() || !startsAt || !endsAt || busy} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">创建会议</button>
        </div>
        <div className="mt-4 space-y-2">{meetings.map((meeting) => <div key={meeting.id} className="rounded-2xl bg-slate-50 p-3"><div className="text-sm font-medium text-slate-800">{meeting.title}</div><div className="mt-1 text-xs text-slate-500">{meeting.startsAt} → {meeting.endsAt} · {meeting.status}</div></div>)}</div>
      </article>

      <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><CalendarDays className="h-4 w-4 text-indigo-600" />任务 / 会议派生日历</div>
        {loading ? <div className="mt-4 flex items-center text-sm text-slate-500"><LoaderCircle className="mr-2 h-4 w-4 animate-spin" />读取派生项</div> : <div className="mt-4 grid gap-2 md:grid-cols-2">{calendar.map((entry) => <div key={entry.id} className="rounded-2xl border border-slate-100 p-3"><div className="text-xs font-semibold text-indigo-700">{entry.target_kind === 'task' ? '任务' : '会议'} · v{entry.source_version}</div><div className="mt-1 text-sm text-slate-700">{entry.starts_at}{entry.ends_at ? ` → ${entry.ends_at}` : ''}</div></div>)}</div>}
      </article>
    </section>
  );
}
