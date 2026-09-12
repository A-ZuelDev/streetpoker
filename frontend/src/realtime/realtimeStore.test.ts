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
    store.getState().receiveCommandError({
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
});
