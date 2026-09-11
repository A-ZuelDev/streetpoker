import { useState } from 'react';

import { formatChips } from './formatChips';
import type { LegalActionsView, OccupiedSeatView } from './table.types';

interface ActionBarProps {
  hero: OccupiedSeatView;
  legalActions: LegalActionsView | null;
  canStartHand: boolean;
}

function clampAmount(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function authoritativeActionKey(legalActions: LegalActionsView | null): string {
  const wager = legalActions?.wager ?? null;

  return [
    legalActions?.canFold ?? false,
    legalActions?.canCheck ?? false,
    legalActions?.call?.chips ?? null,
    legalActions?.call?.total ?? null,
    wager?.kind ?? null,
    wager?.minimum ?? null,
    wager?.maximum ?? null,
    wager?.initial ?? null,
  ].join(':');
}

function ActionBarForAuthoritativeState({
  hero,
  legalActions,
  canStartHand,
}: ActionBarProps) {
  const wager = legalActions?.wager ?? null;
  const authoritativeAmount =
    wager === null
      ? 0
      : clampAmount(wager.initial, wager.minimum, wager.maximum);
  const [amount, setAmount] = useState(authoritativeAmount);
  const [amountDraft, setAmountDraft] = useState(String(authoritativeAmount));

  const updateAmount = (value: number) => {
    if (wager === null) {
      return;
    }
    const nextValue = Number.isFinite(value) ? value : wager.minimum;
    const clampedValue = clampAmount(nextValue, wager.minimum, wager.maximum);
    setAmount(clampedValue);
    setAmountDraft(String(clampedValue));
  };

  const updateAmountDraft = (value: string) => {
    setAmountDraft(value);

    if (wager === null || value.trim() === '') {
      return;
    }

    const nextValue = Number(value);
    if (
      Number.isFinite(nextValue) &&
      nextValue >= wager.minimum &&
      nextValue <= wager.maximum
    ) {
      setAmount(nextValue);
    }
  };

  const commitAmountDraft = () => {
    const nextValue =
      amountDraft.trim() === '' ? Number.NaN : Number(amountDraft);
    updateAmount(nextValue);
  };

  if (legalActions === null) {
    return (
      <section
        className="action-bar action-bar--waiting"
        aria-label="Table actions"
      >
        <div className="action-hero">
          <span className="action-hero__eyebrow">Your seat</span>
          <strong>{hero.nickname}</strong>
          <span>{formatChips(hero.stack)} chips</span>
        </div>
        <div className="action-waiting-copy">
          <strong>Table ready</strong>
          <span>Four players seated · Host can begin</span>
        </div>
        <button
          className="action-button action-button--primary action-button--start"
          type="button"
          disabled={!canStartHand}
          title="Demo only"
        >
          Start hand
        </button>
      </section>
    );
  }

  const passiveAction = legalActions.canCheck
    ? 'Check'
    : legalActions.call === null
      ? 'Call'
      : `Call ${formatChips(legalActions.call.chips)}`;

  return (
    <section className="action-bar" aria-label="Table actions">
      <div className="action-hero">
        <span className="action-hero__eyebrow">Your turn</span>
        <strong>{hero.nickname}</strong>
        <span>{formatChips(hero.stack)} behind</span>
      </div>

      <p className="action-summary" aria-live="polite">
        <span>To call</span>
        <strong>{formatChips(legalActions.call?.chips ?? 0)}</strong>
      </p>

      <div className="action-buttons action-buttons--quick">
        <button
          className="action-button action-button--fold"
          type="button"
          disabled={!legalActions.canFold}
          title="Demo only"
        >
          Fold
        </button>
        <button
          className="action-button action-button--neutral"
          type="button"
          disabled={!legalActions.canCheck && legalActions.call === null}
          title="Demo only"
        >
          {passiveAction}
        </button>
      </div>

      <div className="wager-control">
        <div className="wager-control__topline">
          <label htmlFor="wager-amount">
            {wager?.kind === 'bet' ? 'Bet to' : 'Raise to'}
          </label>
          <input
            id="wager-amount"
            aria-label="Bet or raise amount"
            type="number"
            min={wager?.minimum}
            max={wager?.maximum}
            value={amountDraft}
            disabled={wager === null}
            onChange={(event) => updateAmountDraft(event.currentTarget.value)}
            onBlur={commitAmountDraft}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                commitAmountDraft();
              }
            }}
          />
        </div>
        <label className="sr-only" htmlFor="wager-slider">
          Bet or raise amount slider
        </label>
        <input
          id="wager-slider"
          className="wager-control__slider"
          type="range"
          min={wager?.minimum ?? 0}
          max={wager?.maximum ?? 0}
          value={amount}
          disabled={wager === null}
          onChange={(event) => updateAmount(event.currentTarget.valueAsNumber)}
        />
        <div className="wager-control__bounds">
          <button
            type="button"
            disabled={wager === null}
            onClick={() => wager !== null && updateAmount(wager.minimum)}
          >
            Min {wager === null ? '—' : formatChips(wager.minimum)}
          </button>
          <button
            type="button"
            disabled={wager === null}
            onClick={() => wager !== null && updateAmount(wager.maximum)}
          >
            Max {wager === null ? '—' : formatChips(wager.maximum)}
          </button>
        </div>
      </div>

      <button
        className="action-button action-button--primary action-button--wager"
        type="button"
        disabled={wager === null}
        title="Demo only"
        aria-label={`${wager?.kind === 'bet' ? 'Bet' : 'Raise'} ${formatChips(amount)}`}
        onClick={commitAmountDraft}
      >
        {wager?.kind === 'bet' ? 'Bet' : 'Raise'}
        <span>{formatChips(amount)}</span>
      </button>
    </section>
  );
}

export function ActionBar(props: ActionBarProps) {
  return (
    <ActionBarForAuthoritativeState
      key={authoritativeActionKey(props.legalActions)}
      {...props}
    />
  );
}
