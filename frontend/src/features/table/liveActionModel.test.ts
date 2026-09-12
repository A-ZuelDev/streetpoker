import { describe, expect, it } from 'vitest';

import {
  activeRoomSnapshot,
  completedRoomSnapshot,
} from '../../test/roomSnapshots';
import { deriveLiveActionModel } from './liveActionModel';

function model(
  overrides: Partial<Parameters<typeof deriveLiveActionModel>[0]> = {},
) {
  const snapshot = activeRoomSnapshot();
  return deriveLiveActionModel({
    activeHand: snapshot.active_hand,
    viewerGuestId: 'guest_host',
    viewerHasSeat: true,
    currentActorNickname: 'Mara',
    connectionStatus: 'connected',
    pendingCommand: null,
    hasCompletedHand: false,
    ...overrides,
  });
}

describe('deriveLiveActionModel', () => {
  it('maps only server kinds and uses call.chips rather than amount_to_call', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.legal_actions.call!.chips = 37;
    snapshot.active_hand!.legal_actions.amount_to_call = 999;
    const result = model({ activeHand: snapshot.active_hand });

    expect(result.fold).toMatchObject({ label: 'Fold', enabled: true });
    expect(result.middle).toMatchObject({ label: 'Call 37', enabled: true });
    expect(result.wager).toMatchObject({
      commandType: 'raise_to',
      minimumFullTo: 200,
      maximumTo: 9_900,
      selection: 'range',
    });
  });

  it('maps check without inventing a call', () => {
    const hand = activeRoomSnapshot().active_hand!;
    hand.legal_actions.kinds = ['fold', 'check', 'bet'];
    hand.legal_actions.call = null;
    hand.legal_actions.raise_to = null;
    hand.legal_actions.bet_to = {
      minimum_full_to: 100,
      maximum_to: 9_850,
      short_all_in_to: null,
    };
    const result = model({ activeHand: hand });
    expect(result.middle?.label).toBe('Check');
    expect(result.wager?.commandType).toBe('bet_to');
  });

  it.each([
    ['actor mismatch', { viewerGuestId: 'guest_alice' }],
    ['unseated', { viewerHasSeat: false }],
    ['disconnected', { connectionStatus: 'disconnected' as const }],
    ['syncing', { connectionStatus: 'syncing' as const }],
    ['no hand', { activeHand: null }],
  ])('fails closed for %s', (_label, overrides) => {
    const result = model(overrides);
    expect(result.fold).toBeNull();
    expect(result.middle).toBeNull();
    expect(result.wager).toBeNull();
  });

  it('disables every action while one command is pending', () => {
    const result = model({
      pendingCommand: {
        commandId: 'pending',
        type: 'call',
        handNumber: 1,
        expectedActionSequence: 2,
        actorGuestId: 'guest_host',
      },
    });
    expect(result.fold?.enabled).toBe(false);
    expect(result.middle?.enabled).toBe(false);
    expect(result.wager?.enabled).toBe(false);
    expect(result.statusDetail).toBe('Submitting call…');
  });

  it('fails closed on both check/call or both bet/raise', () => {
    const hand = activeRoomSnapshot().active_hand!;
    hand.legal_actions.kinds.push('check');
    let result = model({ activeHand: hand });
    expect(result.protocolWarning).toBe(true);
    expect(result.fold).toBeNull();

    const wagerHand = activeRoomSnapshot().active_hand!;
    wagerHand.legal_actions.kinds.push('bet');
    wagerHand.legal_actions.bet_to = {
      minimum_full_to: 100,
      maximum_to: 200,
      short_all_in_to: null,
    };
    result = model({ activeHand: wagerHand });
    expect(result.protocolWarning).toBe(true);
  });

  it('models min=max as a fixed range and short all-in as discrete-only', () => {
    const equal = activeRoomSnapshot().active_hand!;
    equal.legal_actions.raise_to = {
      minimum_full_to: 200,
      maximum_to: 200,
      short_all_in_to: null,
    };
    expect(model({ activeHand: equal }).wager).toMatchObject({
      selection: 'range',
      minimumFullTo: 200,
      maximumTo: 200,
    });

    const short = activeRoomSnapshot().active_hand!;
    short.legal_actions.raise_to = {
      minimum_full_to: 200,
      maximum_to: 150,
      short_all_in_to: 150,
    };
    expect(model({ activeHand: short }).wager).toMatchObject({
      selection: 'fixed',
      initialTotalTo: 150,
      shortAllInTo: 150,
    });
  });

  it('fails closed on malformed short-all-in bounds', () => {
    const hand = activeRoomSnapshot().active_hand!;
    hand.legal_actions.raise_to = {
      minimum_full_to: 200,
      maximum_to: 150,
      short_all_in_to: 149,
    };
    expect(model({ activeHand: hand }).protocolWarning).toBe(true);
  });

  it('uses completed-hand and waiting labels from server state', () => {
    expect(
      model({
        activeHand: completedRoomSnapshot().active_hand,
        hasCompletedHand: true,
      }).statusLabel,
    ).toBe('Hand complete');
    expect(
      model({ viewerGuestId: 'guest_alice', currentActorNickname: null })
        .statusLabel,
    ).toBe('Waiting for action');
  });

  it('changes context keys only when authoritative action context changes', () => {
    const first = activeRoomSnapshot().active_hand!;
    const original = model({ activeHand: first }).wager!.contextKey;
    expect(
      model({ activeHand: structuredClone(first) }).wager!.contextKey,
    ).toBe(original);
    for (const mutate of [
      (hand: typeof first) => (hand.hand_number += 1),
      (hand: typeof first) => (hand.action_sequence += 1),
      (hand: typeof first) => {
        hand.current_actor = 'guest_alice';
        hand.legal_actions.actor = 'guest_alice';
      },
      (hand: typeof first) => (hand.legal_actions.raise_to!.maximum_to -= 1),
    ]) {
      const changed = structuredClone(first);
      mutate(changed);
      expect(
        model({ activeHand: changed, viewerGuestId: changed.current_actor })
          .wager?.contextKey,
      ).not.toBe(original);
    }
  });
});
