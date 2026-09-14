import type { ConnectionStatus } from '../../realtime/realtimeStore';
import {
  legalActionFactsAreConsistent,
  pokerActionContextKey,
  type ActiveHand,
  type PendingPokerCommand,
  type WagerBounds,
} from '../../realtime/pokerActions';
import { formatChips } from './formatChips';
import type {
  LiveActionButtonView,
  LiveActionsView,
  LiveWagerView,
} from './table.types';

interface LiveActionModelOptions {
  readonly activeHand: ActiveHand | null;
  readonly viewerGuestId: string;
  readonly viewerHasSeat: boolean;
  readonly currentActorNickname: string | null;
  readonly connectionStatus: ConnectionStatus;
  readonly pendingCommand: PendingPokerCommand | null;
  readonly roomCommandPending?: boolean;
  readonly hasCompletedHand: boolean;
}

function waitingModel(
  status: LiveActionsView['status'],
  statusLabel: string,
  statusDetail: string,
  protocolWarning = false,
): LiveActionsView {
  return {
    status,
    statusLabel,
    statusDetail,
    protocolWarning,
    pending: false,
    fold: null,
    middle: null,
    wager: null,
  };
}

function actionButton<Type extends LiveActionButtonView['type']>(
  hand: ActiveHand,
  type: Type,
  label: string,
  enabled: boolean,
): LiveActionButtonView<Type> {
  return {
    type,
    label,
    contextKey: pokerActionContextKey(hand, type),
    enabled,
  };
}

function wagerView(
  hand: ActiveHand,
  bounds: WagerBounds,
  commandType: 'bet_to' | 'raise_to',
  label: 'Bet' | 'Raise',
  enabled: boolean,
): LiveWagerView {
  const common = {
    commandType,
    label,
    contextKey: pokerActionContextKey(hand, commandType),
    minimumFullTo: bounds.minimum_full_to,
    maximumTo: bounds.maximum_to,
    enabled,
  } as const;
  if (bounds.short_all_in_to === null) {
    return {
      ...common,
      selection: 'range',
      shortAllInTo: null,
      initialTotalTo: bounds.minimum_full_to,
    };
  }
  return {
    ...common,
    selection: 'fixed',
    shortAllInTo: bounds.short_all_in_to,
    initialTotalTo: bounds.short_all_in_to,
  };
}

export function deriveLiveActionModel(
  options: LiveActionModelOptions,
): LiveActionsView {
  const {
    activeHand,
    viewerGuestId,
    viewerHasSeat,
    currentActorNickname,
    connectionStatus,
    pendingCommand,
    roomCommandPending = false,
    hasCompletedHand,
  } = options;

  if (connectionStatus === 'connecting' || connectionStatus === 'syncing') {
    return waitingModel(
      'syncing',
      'Syncing',
      'Waiting for a fresh table update',
    );
  }
  if (connectionStatus !== 'connected') {
    return waitingModel(
      'disconnected',
      'Disconnected',
      'Actions are unavailable',
    );
  }
  if (!viewerHasSeat) {
    return waitingModel('watching', 'Watching the table', 'Waiting for a seat');
  }
  if (activeHand === null) {
    return waitingModel(
      'open',
      hasCompletedHand ? 'Hand complete' : 'Table open',
      'Waiting for the next hand',
    );
  }
  if (!legalActionFactsAreConsistent(activeHand)) {
    return waitingModel(
      'waiting',
      'Action controls unavailable',
      'The server sent inconsistent action controls.',
      true,
    );
  }
  if (
    viewerGuestId !== activeHand.current_actor ||
    viewerGuestId !== activeHand.legal_actions.actor
  ) {
    return waitingModel(
      'waiting',
      currentActorNickname === null
        ? 'Waiting for action'
        : `Waiting for ${currentActorNickname}`,
      'Hand in progress',
    );
  }

  const kinds = new Set(activeHand.legal_actions.kinds);
  const enabled = pendingCommand === null && !roomCommandPending;
  const fold = kinds.has('fold')
    ? actionButton(activeHand, 'fold', 'Fold', enabled)
    : null;
  const middle = kinds.has('check')
    ? actionButton(activeHand, 'check', 'Check', enabled)
    : kinds.has('call') && activeHand.legal_actions.call !== null
      ? actionButton(
          activeHand,
          'call',
          `Call ${formatChips(activeHand.legal_actions.call.chips)}`,
          enabled,
        )
      : null;

  let wager: LiveWagerView | null = null;
  if (kinds.has('bet') && activeHand.legal_actions.bet_to !== null) {
    wager = wagerView(
      activeHand,
      activeHand.legal_actions.bet_to,
      'bet_to',
      'Bet',
      enabled,
    );
  } else if (kinds.has('raise') && activeHand.legal_actions.raise_to !== null) {
    wager = wagerView(
      activeHand,
      activeHand.legal_actions.raise_to,
      'raise_to',
      'Raise',
      enabled,
    );
  }

  return {
    status: 'your-turn',
    statusLabel: 'Your turn',
    statusDetail:
      pendingCommand === null
        ? 'Choose an action'
        : `Submitting ${pendingCommand.type.replace('_to', '')}…`,
    protocolWarning: false,
    pending: pendingCommand !== null,
    fold,
    middle,
    wager,
  };
}
