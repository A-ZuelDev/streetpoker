import { useMemo, useState, type FormEvent } from 'react';

import type { RoomCommandRequest } from '../../realtime/roomCommands';
import { formatChips } from '../table/formatChips';
import type { MemberView, SessionAccountingView } from '../table/table.types';

interface StackAdjustmentFormProps {
  members: readonly MemberView[];
  disabled: boolean;
  onRoomCommand?: (request: RoomCommandRequest) => boolean;
}

type AdjustmentType = 'rebuy' | 'cash_out' | 'correction';

const adjustmentLabels: Record<AdjustmentType, string> = {
  rebuy: 'Rebuy / top-up',
  cash_out: 'Cash out / remove',
  correction: 'Correction',
};

export function StackAdjustmentForm({
  members,
  disabled,
  onRoomCommand,
}: StackAdjustmentFormProps) {
  const adjustable = useMemo(
    () =>
      members.filter(
        (member): member is MemberView & { guestId: string; stack: number } =>
          member.guestId !== undefined && member.stack !== null,
      ),
    [members],
  );
  const [targetGuestId, setTargetGuestId] = useState(
    () => adjustable[0]?.guestId ?? '',
  );
  const [adjustmentType, setAdjustmentType] = useState<AdjustmentType>('rebuy');
  const [amountText, setAmountText] = useState('');
  const [reason, setReason] = useState('');

  const selectedTargetGuestId = adjustable.some(
    (member) => member.guestId === targetGuestId,
  )
    ? targetGuestId
    : (adjustable[0]?.guestId ?? '');

  const target = adjustable.find(
    (member) => member.guestId === selectedTargetGuestId,
  );
  const amount = Number(amountText);
  const amountIsValid =
    amountText.trim() !== '' &&
    Number.isSafeInteger(amount) &&
    (adjustmentType === 'correction' ? amount !== 0 : amount > 0);
  const delta = adjustmentType === 'cash_out' ? -amount : amount;
  const resultingStack = target === undefined ? null : target.stack + delta;
  const previewIsValid =
    amountIsValid &&
    resultingStack !== null &&
    Number.isSafeInteger(resultingStack) &&
    resultingStack >= 0;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (target === undefined || !previewIsValid) {
      return;
    }
    if (
      onRoomCommand?.({
        type: 'adjust_stack',
        targetGuestId: target.guestId,
        adjustmentType,
        amount,
        reason: reason.trim() || null,
      })
    ) {
      setAmountText('');
      setReason('');
    }
  };

  if (adjustable.length === 0) {
    return (
      <p className="room-panel__empty">No player has a session stack yet.</p>
    );
  }

  return (
    <form className="stack-adjustment" onSubmit={submit}>
      <label>
        Player
        <select
          value={selectedTargetGuestId}
          disabled={disabled}
          onChange={(event) => setTargetGuestId(event.currentTarget.value)}
        >
          {adjustable.map((member) => (
            <option key={member.guestId} value={member.guestId}>
              {member.nickname} — {formatChips(member.stack)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Type
        <select
          value={adjustmentType}
          disabled={disabled}
          onChange={(event) => {
            setAdjustmentType(event.currentTarget.value as AdjustmentType);
            setAmountText('');
          }}
        >
          <option value="rebuy">Rebuy / top-up</option>
          <option value="cash_out">Cash out / remove</option>
          <option value="correction">Signed correction</option>
        </select>
      </label>
      <label>
        {adjustmentType === 'correction' ? 'Signed amount' : 'Amount'}
        <input
          type="number"
          inputMode="numeric"
          step="1"
          min={adjustmentType === 'correction' ? undefined : 1}
          value={amountText}
          disabled={disabled}
          placeholder={
            adjustmentType === 'correction' ? 'e.g. -100' : 'e.g. 1000'
          }
          onChange={(event) => setAmountText(event.currentTarget.value)}
        />
      </label>
      <label>
        Reason <small>Optional</small>
        <input
          type="text"
          maxLength={80}
          value={reason}
          disabled={disabled}
          placeholder="Short audit note"
          onChange={(event) => setReason(event.currentTarget.value)}
        />
      </label>
      <p className="stack-adjustment__preview" aria-live="polite">
        {resultingStack === null || amountText.trim() === ''
          ? 'Enter an amount to preview the resulting stack.'
          : previewIsValid
            ? `Resulting stack: ${formatChips(resultingStack)}`
            : 'The resulting stack must be a nonnegative safe integer.'}
      </p>
      <button type="submit" disabled={disabled || !previewIsValid}>
        Apply ledgered adjustment
      </button>
    </form>
  );
}

export function SessionAccounting({
  session,
}: {
  session: SessionAccountingView;
}) {
  return (
    <div className="session-accounting">
      {session.players.length === 0 ? (
        <p className="room-panel__empty">No session stacks yet.</p>
      ) : (
        <ul className="session-summary" aria-label="Player session summaries">
          {session.players.map((player) => (
            <li key={`${player.nickname}-${player.seatNumber ?? 'standing'}`}>
              <strong>{player.nickname}</strong>
              <span>
                {player.seatNumber === null
                  ? 'Standing'
                  : `Seat ${player.seatNumber}`}
              </span>
              <dl>
                <div>
                  <dt>Stack</dt>
                  <dd>{formatChips(player.currentStack)}</dd>
                </div>
                <div>
                  <dt>Added</dt>
                  <dd>+{formatChips(player.externalAdded)}</dd>
                </div>
                <div>
                  <dt>Removed</dt>
                  <dd>-{formatChips(player.externalRemoved)}</dd>
                </div>
                <div>
                  <dt>Poker net</dt>
                  <dd
                    className={
                      player.pokerNet < 0 ? 'is-negative' : 'is-positive'
                    }
                  >
                    {player.pokerNet > 0 ? '+' : ''}
                    {formatChips(player.pokerNet)}
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      )}

      <h3>Adjustment history</h3>
      {session.adjustments.length === 0 ? (
        <p className="room-panel__empty">No stack adjustments yet.</p>
      ) : (
        <ol
          className="adjustment-history"
          aria-label="Stack adjustment history"
        >
          {session.adjustments.map((entry) => (
            <li key={entry.sequence}>
              <span>
                <strong>#{entry.sequence}</strong> {entry.targetNickname}
                {entry.targetSeatNumber === null
                  ? ''
                  : ` · Seat ${entry.targetSeatNumber}`}
                {` · ${adjustmentLabels[entry.type]}`}
              </span>
              <span className={entry.delta < 0 ? 'is-negative' : 'is-positive'}>
                {entry.delta > 0 ? '+' : ''}
                {formatChips(entry.delta)} → {formatChips(entry.resultingStack)}
              </span>
              {entry.reason === null ? null : <small>{entry.reason}</small>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
