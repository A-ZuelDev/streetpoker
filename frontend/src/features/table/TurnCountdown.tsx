import { useEffect, useState } from 'react';

interface TurnCountdownProps {
  deadlineUnixMs: number;
}

export function TurnCountdown({ deadlineUnixMs }: TurnCountdownProps) {
  const [nowUnixMs, setNowUnixMs] = useState(Date.now);

  useEffect(() => {
    const interval = window.setInterval(() => setNowUnixMs(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, []);

  const secondsRemaining = Math.ceil(
    Math.max(0, deadlineUnixMs - nowUnixMs) / 1000,
  );

  return (
    <div
      className="turn-countdown"
      role="timer"
      aria-label="Approximate turn time remaining"
      aria-live="off"
    >
      <span>Approx. turn time</span>
      <strong>{secondsRemaining}s</strong>
      {secondsRemaining === 0 ? <small>Awaiting table update</small> : null}
    </div>
  );
}
