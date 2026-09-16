import { describe, expect, it } from 'vitest';

import { activeRoomSnapshot, openRoomSnapshot } from '../test/roomSnapshots';
import { createRealtimeStore } from './realtimeStore';
import { connectionSessionError } from './sessionError';

describe('realtimeStore', () => {
  it('requires state after connected before marking the session fresh', () => {
    const store = createRealtimeStore();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');

    expect(store.getState().status).toBe('syncing');
    expect(store.getState().snapshot).toBeNull();

    store.getState().replaceSnapshot(openRoomSnapshot());
    expect(store.getState().status).toBe('connected');
  });

  it('retains stale state through same-room disconnect and reconnect', () => {
    const store = createRealtimeStore();
    const snapshot = openRoomSnapshot();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');
    store.getState().replaceSnapshot(snapshot);
    store.getState().markDisconnected();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');

    expect(store.getState().status).toBe('syncing');
    expect(store.getState().snapshot).toBe(snapshot);
  });

  it('clears state when a different room begins', () => {
    const store = createRealtimeStore();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');
    store.getState().replaceSnapshot(openRoomSnapshot());

    store.getState().beginConnection('BCDEFGHJ');

    expect(store.getState()).toMatchObject({
      status: 'connecting',
      roomCode: 'BCDEFGHJ',
      guestId: null,
      snapshot: null,
    });
  });

  it('replaces snapshots and never mutates them for acknowledgements or errors', () => {
    const store = createRealtimeStore();
    const first = openRoomSnapshot();
    const second = activeRoomSnapshot();
    store.getState().replaceSnapshot(first);
    store.getState().replaceSnapshot(second);
    store.getState().receiveCommandAck('one');
    store.getState().receivePokerCommandError({
      commandId: 'one',
      code: 'rejected',
      message: 'The server rejected a room command.',
    });

    expect(store.getState().snapshot).toBe(second);
    expect(store.getState().snapshot).not.toMatchObject({ last_hand: first });
  });

  it('keeps a typed error and snapshot when the connection closes', () => {
    const store = createRealtimeStore();
    const snapshot = openRoomSnapshot();
    const error = connectionSessionError('room_closed');
    store.getState().replaceSnapshot(snapshot);
    store.getState().receiveConnectionError(error);
    store.getState().markDisconnected();

    expect(store.getState()).toMatchObject({
      status: 'disconnected',
      snapshot,
      lastConnectionError: error,
    });
  });

  it('resets only on an explicit reset', () => {
    const store = createRealtimeStore();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().resetSession();

    expect(store.getState()).toMatchObject({
      status: 'idle',
      roomCode: null,
      guestId: null,
      snapshot: null,
    });
  });

  it('admits only one pending command synchronously and ack retains it', () => {
    const store = createRealtimeStore();
    const pending = {
      commandId: 'one',
      type: 'call' as const,
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    };
    expect(store.getState().tryBeginPokerCommand(pending)).toBe(true);
    expect(
      store.getState().tryBeginPokerCommand({ ...pending, commandId: 'two' }),
    ).toBe(false);
    store.getState().receiveCommandAck('one');
    expect(store.getState().pendingPokerCommand).toEqual(pending);
  });

  it('clears only a matching command error and never mutates the snapshot', () => {
    const store = createRealtimeStore();
    const snapshot = activeRoomSnapshot();
    store.getState().replaceSnapshot(snapshot);
    store.getState().tryBeginPokerCommand({
      commandId: 'one',
      type: 'call',
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    });
    store.getState().receivePokerCommandError({
      commandId: 'other',
      code: 'illegal_call',
      message: 'safe',
    });
    expect(store.getState().pendingPokerCommand?.commandId).toBe('one');
    store.getState().receivePokerCommandError({
      commandId: 'one',
      code: 'illegal_call',
      message: 'Calling is not available now.',
    });
    expect(store.getState().pendingPokerCommand).toBeNull();
    expect(store.getState().snapshot).toBe(snapshot);
  });

  it('expires a poker error when authoritative action context advances', () => {
    const store = createRealtimeStore();
    const snapshot = activeRoomSnapshot();
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');
    store.getState().replaceSnapshot(snapshot);
    store.getState().tryBeginPokerCommand({
      commandId: 'one',
      type: 'call',
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    });
    store.getState().receivePokerCommandError({
      commandId: 'one',
      code: 'illegal_call',
      message: 'Calling is not available now.',
    });
    store.getState().replaceSnapshot(structuredClone(snapshot));
    expect(store.getState().lastPokerCommandError).not.toBeNull();

    const advanced = structuredClone(snapshot);
    advanced.active_hand!.action_sequence = 3;
    advanced.active_hand!.current_actor = 'guest_alice';
    store.getState().replaceSnapshot(advanced);
    expect(store.getState().lastPokerCommandError).toBeNull();

    store.getState().replaceSnapshot(structuredClone(snapshot));
    expect(store.getState().lastPokerCommandError).toBeNull();
  });

  it('resolves pending only when authoritative action context changes', () => {
    const store = createRealtimeStore();
    const snapshot = activeRoomSnapshot();
    const begin = () =>
      store.getState().tryBeginPokerCommand({
        commandId: crypto.randomUUID(),
        type: 'call',
        handNumber: 1,
        expectedActionSequence: 2,
        actorGuestId: 'guest_host',
      });

    begin();
    store.getState().replaceSnapshot(structuredClone(snapshot));
    expect(store.getState().pendingPokerCommand).not.toBeNull();

    const sequence = structuredClone(snapshot);
    sequence.active_hand!.action_sequence = 3;
    store.getState().replaceSnapshot(sequence);
    expect(store.getState().pendingPokerCommand).toBeNull();

    begin();
    const actor = structuredClone(snapshot);
    actor.active_hand!.current_actor = 'guest_alice';
    actor.active_hand!.legal_actions.actor = 'guest_alice';
    store.getState().replaceSnapshot(actor);
    expect(store.getState().pendingPokerCommand).toBeNull();

    begin();
    const hand = structuredClone(snapshot);
    hand.active_hand!.hand_number = 2;
    store.getState().replaceSnapshot(hand);
    expect(store.getState().pendingPokerCommand).toBeNull();

    begin();
    store.getState().replaceSnapshot(openRoomSnapshot());
    expect(store.getState().pendingPokerCommand).toBeNull();
  });

  it('clears pending across disconnect and reconnect without replay state', () => {
    const store = createRealtimeStore();
    store.getState().tryBeginPokerCommand({
      commandId: 'one',
      type: 'fold',
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    });
    store.getState().markDisconnected();
    expect(store.getState().pendingPokerCommand).toBeNull();
    store.getState().beginConnection('ABCDEFGH');
    expect(store.getState().pendingPokerCommand).toBeNull();
  });

  it('holds room pending through matching ack until a later state', () => {
    const store = createRealtimeStore();
    const first = openRoomSnapshot();
    store.getState().replaceSnapshot(first);
    expect(
      store.getState().tryBeginRoomCommand({
        commandId: 'room-one',
        type: 'request_seat',
        seatIndex: 0,
        acknowledged: false,
      }),
    ).toBe(true);

    const preAck = structuredClone(first);
    preAck.room.settings.room_name = 'Unrelated state';
    store.getState().replaceSnapshot(preAck);
    expect(store.getState().pendingRoomCommand?.acknowledged).toBe(false);

    store.getState().receiveCommandAck('other');
    expect(store.getState().pendingRoomCommand?.acknowledged).toBe(false);
    store.getState().receiveCommandAck('room-one');
    expect(store.getState().pendingRoomCommand?.acknowledged).toBe(true);
    expect(store.getState().snapshot).toBe(preAck);

    const postAck = structuredClone(preAck);
    postAck.room.seat_requests = [
      { guest_id: 'guest_host', nickname: 'Mara', seat_index: 0 },
    ];
    store.getState().replaceSnapshot(postAck);
    expect(store.getState().pendingRoomCommand).toBeNull();
    expect(store.getState().snapshot).toBe(postAck);
  });

  it('enforces a synchronous mutex across room and poker command families', () => {
    const store = createRealtimeStore();
    const room = {
      commandId: 'room',
      type: 'stand' as const,
      acknowledged: false,
    };
    const poker = {
      commandId: 'poker',
      type: 'fold' as const,
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    };
    expect(store.getState().tryBeginRoomCommand(room)).toBe(true);
    expect(store.getState().tryBeginPokerCommand(poker)).toBe(false);
    store.getState().cancelPendingRoomCommand('room');
    expect(store.getState().tryBeginPokerCommand(poker)).toBe(true);
    expect(store.getState().tryBeginRoomCommand(room)).toBe(false);
  });

  it('clears only a matching room error and never mutates the snapshot', () => {
    const store = createRealtimeStore();
    const snapshot = openRoomSnapshot();
    store.getState().replaceSnapshot(snapshot);
    store.getState().tryBeginRoomCommand({
      commandId: 'room',
      type: 'stand',
      acknowledged: false,
    });
    store.getState().receiveRoomCommandError({
      commandId: 'other',
      code: 'not_seated',
      message: 'safe',
    });
    expect(store.getState().pendingRoomCommand?.commandId).toBe('room');
    store.getState().receiveRoomCommandError({
      commandId: 'room',
      code: 'not_seated',
      message: 'You are not currently seated.',
    });
    expect(store.getState().pendingRoomCommand).toBeNull();
    expect(store.getState().snapshot).toBe(snapshot);
  });

  it('ends a room session without retaining actionable identity or snapshots', () => {
    const store = createRealtimeStore();
    store.getState().beginConnection('ABCDEFGH');
    store.getState().receiveConnected('guest_host', 'ABCDEFGH');
    store.getState().replaceSnapshot(openRoomSnapshot());
    store.getState().endRoomSession({
      kind: 'kicked',
      roomCode: 'ABCDEFGH',
      roomName: 'Friday Night',
    });
    expect(store.getState()).toMatchObject({
      status: 'idle',
      roomCode: null,
      guestId: null,
      snapshot: null,
      pendingPokerCommand: null,
      pendingRoomCommand: null,
      roomExit: { kind: 'kicked' },
    });
  });
});
