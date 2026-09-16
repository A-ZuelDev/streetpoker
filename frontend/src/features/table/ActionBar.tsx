import { useState } from 'react';

import { formatChips } from './formatChips';
import type { PokerActionRequest } from '../../realtime/pokerActions';
import type {
  HandCompletionView,
  LegalActionsView,
  LiveActionsView,
  LiveWagerView,
  OccupiedSeatView,
} from './table.types';

interface ActionBarProps {
  hero: OccupiedSeatView | null;
  legalActions: LegalActionsView | null;
  canStartHand: boolean;
  isHost: boolean;
  mode: 'demo' | 'live';
  handCompletion: HandCompletionView | null;
  liveActions: LiveActionsView | null;
  commandError: string | null;
  onPokerAction?: (request: PokerActionRequest) => boolean;
  onStartHand?: () => boolean;
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

function LiveActionBar({
  hero,
  liveActions,
  commandError,
  onPokerAction,
  canStartHand,
  isHost,
  handCompletion,
  onStartHand,
}: Pick<
  ActionBarProps,
  | 'hero'
  | 'liveActions'
  | 'commandError'
  | 'onPokerAction'
  | 'canStartHand'
  | 'isHost'
  | 'handCompletion'
  | 'onStartHand'
>) {
  if (liveActions === null) {
    return null;
  }
  const hasControls =
    liveActions.fold !== null ||
    liveActions.middle !== null ||
    liveActions.wager !== null;
  const showStartHand = isHost && liveActions.status === 'open';
  const startHandLabel =
    handCompletion === null ? 'Start hand' : 'Start next hand';

  if (!hasControls) {
    return (
      <section
        className={`action-bar action-bar--waiting action-bar--${liveActions.status} ${showStartHand ? '' : 'action-bar--readonly'}`.trim()}
        aria-label="Table actions"
        aria-busy={liveActions.pending}
      >
        <div className="action-hero">
          <span className="action-hero__eyebrow">
            {hero === null ? 'Room member' : 'Your seat'}
          </span>
          <strong>{hero?.nickname ?? 'Not seated'}</strong>
          <span>
            {hero === null
              ? 'Room observer'
              : `${formatChips(hero.stack)} chips`}
          </span>
        </div>
        <div className="action-waiting-copy">
          <strong>{liveActions.statusLabel}</strong>
          <span>{liveActions.statusDetail}</span>
        </div>
        {showStartHand ? (
          <button
            className="action-button action-button--primary action-button--start"
            type="button"
            disabled={!canStartHand || onStartHand === undefined}
            onClick={onStartHand}
          >
            {startHandLabel}
          </button>
        ) : null}
        {liveActions.protocolWarning ? (
          <p className="action-feedback" role="alert">
            Action controls are unavailable until a fresh valid update arrives.
          </p>
        ) : null}
      </section>
    );
  }

  const submit = (request: PokerActionRequest) => {
    onPokerAction?.(request);
  };
  const foldAction = liveActions.fold;
  const middleAction = liveActions.middle;
  const wagerAction = liveActions.wager;

  return (
    <section
      className={`action-bar action-bar--${liveActions.status}`}
      aria-label="Table actions"
      aria-busy={liveActions.pending}
    >
      <div className="action-hero">
        <span className="action-hero__eyebrow">{liveActions.statusLabel}</span>
        <strong>{hero?.nickname ?? 'Not seated'}</strong>
        <span>
          {hero === null ? 'Watching' : `${formatChips(hero.stack)} behind`}
        </span>
      </div>

      <p
        className="action-summary"
        {...(liveActions.pendingSource === 'room'
          ? {}
          : { 'aria-live': 'polite' as const })}
      >
        <span>Action</span>
        <strong>{liveActions.statusDetail}</strong>
      </p>

      <div className="action-buttons action-buttons--quick">
        {foldAction === null ? null : (
          <button
            className="action-button action-button--fold"
            type="button"
            disabled={!foldAction.enabled || onPokerAction === undefined}
            onClick={() =>
              submit({
                type: 'fold',
                contextKey: foldAction.contextKey,
              })
            }
          >
            Fold
          </button>
        )}
        {middleAction === null ? null : (
          <button
            className="action-button action-button--neutral"
            type="button"
            disabled={!middleAction.enabled || onPokerAction === undefined}
            onClick={() =>
              submit({
                type: middleAction.type,
                contextKey: middleAction.contextKey,
              })
            }
          >
            {middleAction.label}
          </button>
        )}
      </div>
      {wagerAction === null ? (
        <div />
      ) : (
        <LiveWagerControl
          key={wagerAction.contextKey}
          wager={wagerAction}
          canSubmit={onPokerAction !== undefined}
          onSubmit={(totalTo) =>
            submit({
              type: wagerAction.commandType,
              contextKey: wagerAction.contextKey,
              totalTo,
            })
          }
        />
      )}
      {commandError === null ? null : (
        <p className="action-feedback" role="alert">
          {commandError}
        </p>
      )}
    </section>
  );
}

function LiveWagerControl({
  wager,
  canSubmit,
  onSubmit,
}: {
  wager: LiveWagerView;
  canSubmit: boolean;
  onSubmit: (totalTo: number) => void;
}) {
  const [totalTo, setTotalTo] = useState(wager.initialTotalTo);
  const [totalToDraft, setTotalToDraft] = useState(
    String(wager.initialTotalTo),
  );
  const isSingleTotal =
    wager.selection === 'fixed' || wager.minimumFullTo === wager.maximumTo;

  const selectTotalTo = (value: number) => {
    if (wager.selection === 'fixed') {
      setTotalTo(wager.shortAllInTo);
      setTotalToDraft(String(wager.shortAllInTo));
      return wager.shortAllInTo;
    }
    const next = clampAmount(value, wager.minimumFullTo, wager.maximumTo);
    setTotalTo(next);
    setTotalToDraft(String(next));
    return next;
  };

  const commitDraft = () => {
    const parsed =
      totalToDraft.trim() === '' ? Number.NaN : Number(totalToDraft);
    if (!Number.isSafeInteger(parsed)) {
      setTotalToDraft(String(totalTo));
      return null;
    }
    return selectTotalTo(parsed);
  };

  const submit = () => {
    const selected = commitDraft();
    if (
      wager.enabled &&
      canSubmit &&
      selected !== null &&
      Number.isSafeInteger(selected) &&
      (wager.selection === 'fixed'
        ? selected === wager.shortAllInTo
        : selected >= wager.minimumFullTo && selected <= wager.maximumTo)
    ) {
      onSubmit(selected);
    }
  };

  return (
    <>
      <div
        className={`wager-control ${isSingleTotal ? 'wager-control--fixed' : ''}`}
      >
        <div className="wager-control__topline">
          <label htmlFor="live-wager-total">{wager.label} to</label>
          <input
            id="live-wager-total"
            aria-label={`${wager.label} total`}
            type="number"
            step={1}
            min={
              wager.selection === 'range'
                ? wager.minimumFullTo
                : wager.shortAllInTo
            }
            max={wager.maximumTo}
            value={totalToDraft}
            readOnly={isSingleTotal}
            disabled={!wager.enabled || !canSubmit}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setTotalToDraft(value);
              const parsed = Number(value);
              if (
                value.trim() !== '' &&
                Number.isSafeInteger(parsed) &&
                wager.selection === 'range' &&
                parsed >= wager.minimumFullTo &&
                parsed <= wager.maximumTo
              ) {
                setTotalTo(parsed);
              }
            }}
            onBlur={commitDraft}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                submit();
              }
            }}
          />
        </div>
        {wager.selection === 'range' && !isSingleTotal ? (
          <>
            <label className="sr-only" htmlFor="live-wager-slider">
              {wager.label} total slider
            </label>
            <input
              id="live-wager-slider"
              className="wager-control__slider"
              type="range"
              step={1}
              min={wager.minimumFullTo}
              max={wager.maximumTo}
              value={totalTo}
              disabled={!wager.enabled || !canSubmit}
              onChange={(event) =>
                selectTotalTo(event.currentTarget.valueAsNumber)
              }
            />
            <div className="wager-control__bounds">
              <button
                type="button"
                disabled={!wager.enabled || !canSubmit}
                onClick={() => selectTotalTo(wager.minimumFullTo)}
              >
                Min {formatChips(wager.minimumFullTo)}
              </button>
              <button
                type="button"
                disabled={!wager.enabled || !canSubmit}
                onClick={() => selectTotalTo(wager.maximumTo)}
              >
                Max {formatChips(wager.maximumTo)}
              </button>
            </div>
          </>
        ) : (
          <strong className="wager-control__fixed-total">
            {wager.selection === 'fixed' ? 'All-in ' : 'Fixed '}
            {formatChips(wager.initialTotalTo)}
          </strong>
        )}
      </div>
      <button
        className="action-button action-button--primary action-button--wager"
        type="button"
        disabled={!wager.enabled || !canSubmit}
        aria-label={`${wager.label}${wager.selection === 'fixed' ? ' all-in' : ''} ${formatChips(totalTo)}`}
        onClick={submit}
      >
        {wager.label}
        <span>
          {wager.selection === 'fixed' ? 'All-in ' : ''}
          {formatChips(totalTo)}
        </span>
      </button>
    </>
  );
}

function DemoActionBar({ hero, legalActions }: ActionBarProps) {
  if (hero === null) {
    throw new Error('The table demo requires a hero seat.');
  }

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
          <span>Controls preview · Live host can begin</span>
        </div>
        <button
          className="action-button action-button--primary action-button--start"
          type="button"
          disabled
          title="Preview only"
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
          disabled
          title="Preview only"
        >
          Fold
        </button>
        <button
          className="action-button action-button--neutral"
          type="button"
          disabled
          title="Preview only"
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
        disabled
        title="Preview only"
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
  if (props.mode === 'live') {
    return (
      <LiveActionBar
        hero={props.hero}
        liveActions={props.liveActions}
        commandError={props.commandError}
        canStartHand={props.canStartHand}
        isHost={props.isHost}
        handCompletion={props.handCompletion}
        {...(props.onStartHand === undefined
          ? {}
          : { onStartHand: props.onStartHand })}
        {...(props.onPokerAction === undefined
          ? {}
          : { onPokerAction: props.onPokerAction })}
      />
    );
  }

  return (
    <DemoActionBar
      key={authoritativeActionKey(props.legalActions)}
      {...props}
    />
  );
}
