import { useEffect, useMemo, useState } from 'react';
import {
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { useMarketData } from '../hooks/useMarketData';
import { sendMTerminalsWS } from '../services/mterminals-ws';
import { useMarketContext } from '../state/market-context';

type SelectorKind = 'symbol' | 'expiry' | 'broker' | null;

const DATA_SOURCE_KEYS: Record<string, string> = {
  'ANGEL ONE': 'SMARTAPI',
  'SMARTAPI': 'SMARTAPI',

  'UPSTOX': 'UPSTOX',

  'SHOONYA': 'SHOONYA',

  'ICICI DIRECT': 'BREEZE',
  'BREEZE': 'BREEZE',

  'KOTAK NEO': 'KOTAK',
  'KOTAK': 'KOTAK',

  'ZERODHA KITE': 'KITE',
  'KITE': 'KITE',

  'NSE/BSE API': 'NSE_BSE',
  'NSE_BSE': 'NSE_BSE',
};

function normalizeDataSource(value: string): string {
  const normalized = value.trim().toUpperCase();
  return DATA_SOURCE_KEYS[normalized] ?? normalized;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const out: string[] = [];

  for (const item of value) {
    let text = '';

    if (typeof item === 'string') {
      text = item;
    } else if (item && typeof item === 'object') {
      const row = item as Record<string, unknown>;
      const candidate =
        row.name ??
        row.label ??
        row.value ??
        row.source ??
        row.broker ??
        row.expiry;

      if (typeof candidate === 'string') {
        text = candidate;
      }
    }

    text = text.trim();

    if (text && !out.includes(text)) {
      out.push(text);
    }
  }

  return out;
}

export function MarketContextBar() {
  const {
    symbol,
    setSymbol,
    expiry,
    setExpiry,
    broker,
    setBroker,
  } = useMarketContext();

  const { payload, connectionStatus } = useMarketData();

  const [selector, setSelector] = useState<SelectorKind>(null);
  const [symbolQuery, setSymbolQuery] = useState('');

  const market =
    payload?.market && typeof payload.market === 'object'
      ? payload.market
      : payload ?? {};

  const expiryDates = useMemo(() => {
    return asStringList(
      market.expiryDates ??
        market.expiries ??
        payload?.expiryDates,
    );
  }, [market.expiryDates, market.expiries, payload?.expiryDates]);

  const dataSources = useMemo(() => {
    const discovered = asStringList(
      market.dataSources ??
        payload?.dataSources,
    );

    const current =
      typeof market.dataSource === 'string'
        ? market.dataSource
        : typeof payload?.dataSource === 'string'
          ? payload.dataSource
          : '';

    if (current && !discovered.includes(current)) {
      return [current, ...discovered];
    }

    return discovered;
  }, [
    market.dataSource,
    market.dataSources,
    payload,
  ]);

  const backendSymbol =
    typeof market.symbol === 'string'
      ? market.symbol.trim().toUpperCase()
      : typeof payload?.symbol === 'string'
        ? payload.symbol.trim().toUpperCase()
        : '';

  const universe = market.fnoSymbols ?? payload?.fnoSymbols;
  const symbols = useMemo(() => {
    const available = [
      ...asStringList(universe?.indices),
      ...asStringList(universe?.stocks),
      backendSymbol || symbol,
    ];
    return [...new Set(available.map(value => value.trim().toUpperCase()))]
      .filter(Boolean);
  }, [universe, backendSymbol, symbol]);

  const backendExpiry =
    typeof market.expiry === 'string'
      ? market.expiry
      : typeof payload?.expiry === 'string'
        ? payload.expiry
        : null;

  const backendBroker =
    typeof market.dataSource === 'string'
      ? market.dataSource
      : typeof payload?.dataSource === 'string'
        ? payload.dataSource
        : null;

  useEffect(() => {
    if (!expiry && backendExpiry) {
      setExpiry(backendExpiry);
      return;
    }

    if (!expiry && expiryDates.length > 0) {
      setExpiry(expiryDates[0]);
    }
  }, [
    backendExpiry,
    expiry,
    expiryDates,
    setExpiry,
  ]);

  useEffect(() => {
    if (backendSymbol && backendSymbol !== symbol) {
      setSymbol(backendSymbol);
    }
  }, [backendSymbol, symbol, setSymbol]);

  useEffect(() => {
    if (backendBroker && backendBroker !== broker) {
      setBroker(backendBroker);
    }
  }, [backendBroker, broker, setBroker]);

  const options = useMemo(() => {
    if (selector === 'symbol') {
      const query = symbolQuery.trim().toUpperCase();
      return symbols.filter(value => value.includes(query));
    }
    if (selector === 'expiry') return expiryDates;
    if (selector === 'broker') return dataSources;
    return [];
  }, [selector, symbols, symbolQuery, expiryDates, dataSources]);

  const selectorTitle =
    selector === 'symbol'
      ? 'Select Symbol'
      : selector === 'expiry'
        ? 'Select Expiry'
        : selector === 'broker'
          ? 'Select Data Source'
          : '';

  const selectedValue =
    selector === 'symbol'
      ? symbol
      : selector === 'expiry'
        ? expiry
        : selector === 'broker'
          ? broker
          : null;

  const selectValue = (value: string) => {
    if (selector === 'symbol') {
      const requested = value.trim().toUpperCase();
      const active = String(
        backendSymbol || symbol,
      ).trim().toUpperCase();

      if (requested && requested !== active) {
        sendMTerminalsWS({
          type: 'switch_symbol',
          symbol: requested,
          expiry: null,
        });
      }
    } else if (selector === 'expiry') {
      const requestedExpiry = value.trim();
      const activeExpiry = String(
        backendExpiry || expiry || '',
      ).trim();

      if (
        requestedExpiry &&
        requestedExpiry !== activeExpiry
      ) {
        sendMTerminalsWS({
          type: 'switch_symbol',
          symbol: backendSymbol || symbol,
          expiry: requestedExpiry,
        });
      }
    } else if (selector === 'broker') {
      const requested = normalizeDataSource(value);
      const active = normalizeDataSource(broker);

      if (requested && requested !== active) {
        sendMTerminalsWS({
          type: 'switch_data_source',
          dataSource: requested,
        });
      }
    }

    setSelector(null);
  };

  const sourceConnected = connectionStatus === 'connected';

  return (
    <>
      <View style={styles.container}>
        <Pressable
          style={styles.item}
          onPress={() => {
            setSymbolQuery('');
            setSelector('symbol');
          }}
        >
          <Text style={styles.primary}>{symbol} ▾</Text>
        </Pressable>

        <View style={styles.divider} />

        <Pressable
          style={styles.item}
          onPress={() => setSelector('expiry')}
        >
          <Text
            style={styles.secondary}
            numberOfLines={1}
          >
            {expiry ?? backendExpiry ?? 'EXPIRY'} ▾
          </Text>
        </Pressable>

        <View style={styles.divider} />

        <Pressable
          style={styles.item}
          onPress={() => setSelector('broker')}
        >
          <View style={styles.sourceRow}>
            <Text
              style={styles.secondary}
              numberOfLines={1}
            >
              {broker}
            </Text>

            <View
              style={[
                styles.statusDot,
                {
                  backgroundColor: sourceConnected
                    ? '#63d297'
                    : '#e8b45b',
                },
              ]}
            />
          </View>
        </Pressable>
      </View>

      <Modal
        visible={selector !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setSelector(null)}
      >
        <Pressable
          style={styles.backdrop}
          onPress={() => setSelector(null)}
        >
          <Pressable
            style={styles.sheet}
            onPress={(event) => event.stopPropagation()}
          >
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>
                {selectorTitle}
              </Text>

              <Pressable
                onPress={() => setSelector(null)}
                hitSlop={12}
              >
                <Text style={styles.close}>✕</Text>
              </Pressable>
            </View>

            {selector === 'symbol' ? (
              <TextInput
                style={styles.search}
                value={symbolQuery}
                onChangeText={setSymbolQuery}
                placeholder="Search stocks and indices"
                placeholderTextColor="#8f98a5"
                accessibilityLabel="Search stocks and indices"
                autoCapitalize="characters"
                autoCorrect={false}
                clearButtonMode="while-editing"
              />
            ) : null}

            <FlatList
              style={styles.options}
              contentContainerStyle={styles.optionsContent}
              keyboardShouldPersistTaps="handled"
              data={options}
              extraData={selectedValue}
              keyExtractor={value => value}
              renderItem={({ item: value }) => {
                  const active = value === selectedValue;

                  return (
                    <Pressable
                      key={value}
                      style={[
                        styles.option,
                        active && styles.optionActive,
                      ]}
                      onPress={() => selectValue(value)}
                    >
                      <Text
                        style={[
                          styles.optionText,
                          active && styles.optionTextActive,
                        ]}
                      >
                        {value}
                      </Text>

                      {active ? (
                        <Text style={styles.check}>✓</Text>
                      ) : null}
                    </Pressable>
                  );
                }}
              ListEmptyComponent={
                <Text style={styles.empty}>
                  {selector === 'expiry'
                    ? 'Waiting for expiry dates from backend…'
                    : selector === 'broker'
                      ? 'Waiting for available data sources…'
                      : symbolQuery.trim()
                        ? 'No matching symbols.'
                        : 'Waiting for available symbols…'}
                </Text>
              }
            />
          </Pressable>
        </Pressable>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#303640',
    borderRadius: 12,
    marginBottom: 16,
    backgroundColor: '#171a20',
    overflow: 'hidden',
  },

  item: {
    flex: 1,
    minWidth: 0,
    paddingVertical: 13,
    paddingHorizontal: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },

  divider: {
    width: StyleSheet.hairlineWidth,
    alignSelf: 'stretch',
    backgroundColor: '#343a44',
  },

  primary: {
    fontSize: 15,
    fontWeight: '800',
    color: '#f3f5f7',
  },

  secondary: {
    fontSize: 12,
    fontWeight: '700',
    color: '#d8dce2',
  },

  sourceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    maxWidth: '100%',
  },

  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 999,
  },

  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.62)',
  },

  sheet: {
    maxHeight: '68%',
    backgroundColor: '#171a20',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderColor: '#303640',
    paddingBottom: 24,
  },

  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#303640',
  },

  sheetTitle: {
    fontSize: 17,
    fontWeight: '800',
    color: '#f3f5f7',
  },

  close: {
    fontSize: 18,
    color: '#aeb5bf',
  },

  options: {
    flexGrow: 0,
  },

  search: {
    marginHorizontal: 18,
    marginTop: 12,
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderWidth: 1,
    borderColor: '#303640',
    borderRadius: 10,
    color: '#f3f5f7',
    fontSize: 15,
  },

  optionsContent: {
    paddingHorizontal: 12,
    paddingTop: 8,
  },

  option: {
    minHeight: 50,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    borderRadius: 10,
    marginVertical: 3,
  },

  optionActive: {
    backgroundColor: '#262d36',
  },

  optionText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#d8dce2',
  },

  optionTextActive: {
    color: '#ffffff',
    fontWeight: '800',
  },

  check: {
    fontSize: 16,
    fontWeight: '800',
    color: '#63d297',
  },

  empty: {
    paddingHorizontal: 14,
    paddingVertical: 24,
    textAlign: 'center',
    color: '#8f98a5',
    fontSize: 13,
  },
});
