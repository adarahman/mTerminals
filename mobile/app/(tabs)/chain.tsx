import { useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

const RANGE_OPTIONS = [3, 5, 10, 15];

export default function ChainScreen() {
  const { payload, connected, error } = useMarketData();
  const market: any = payload?.market ?? {};

  const chain = Array.isArray(market.chain)
    ? market.chain
    : [];

  const atm = Number(market.atm);
  const [range, setRange] = useState(5);

  const visibleRows = useMemo(() => {
    if (!chain.length) return [];

    const sorted = [...chain].sort(
      (a: any, b: any) =>
        Number(a.strike) - Number(b.strike),
    );

    const atmIndex = sorted.findIndex(
      (row: any) => Number(row.strike) === atm,
    );

    if (atmIndex < 0) {
      return sorted.slice(0, range * 2 + 1);
    }

    const start = Math.max(0, atmIndex - range);
    const end = Math.min(
      sorted.length,
      atmIndex + range + 1,
    );

    return sorted.slice(start, end);
  }, [chain, atm, range]);

  return (
    <SafeAreaView
      style={{
        flex: 1,
        backgroundColor: '#0b0d10',
      }}
      edges={['top']}
    >
      <MarketContextBar />

      <ScrollView
        contentContainerStyle={{
          padding: 14,
          paddingBottom: 40,
          gap: 12,
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
          }}
        >
          <View>
            <Text
              style={{
                color: '#ffffff',
                fontSize: 25,
                fontWeight: '800',
              }}
            >
              Option Chain
            </Text>

            <Text
              style={{
                color: '#7f8a99',
                fontSize: 12,
                marginTop: 4,
              }}
            >
              {market.symbol || '—'}
              {'  •  '}
              {market.expiry || '—'}
              {'  •  ATM '}
              {market.atm ?? '—'}
            </Text>
          </View>

          <Text
            style={{
              color: connected
                ? '#63d297'
                : '#ef7777',
              fontSize: 12,
              fontWeight: '700',
            }}
          >
            {connected ? '● LIVE' : '● OFFLINE'}
          </Text>
        </View>

        {error ? (
          <Card>
            <Text style={{ color: '#ef7777' }}>
              {error}
            </Text>
          </Card>
        ) : null}

        <View
          style={{
            flexDirection: 'row',
            gap: 8,
          }}
        >
          <SummaryChip
            label="SPOT"
            value={formatNumber(market.spot, 2)}
          />

          <SummaryChip
            label="CE WALL"
            value={formatNumber(market.ceWall, 0)}
          />

          <SummaryChip
            label="PE WALL"
            value={formatNumber(market.peWall, 0)}
          />
        </View>

        <Card>
          <Text style={styles.sectionTitle}>
            STRIKE RANGE
          </Text>

          <View
            style={{
              flexDirection: 'row',
              gap: 8,
              marginTop: 10,
            }}
          >
            {RANGE_OPTIONS.map(value => (
              <Pressable
                key={value}
                onPress={() => setRange(value)}
                style={{
                  flex: 1,
                  paddingVertical: 9,
                  borderRadius: 10,
                  alignItems: 'center',
                  backgroundColor:
                    range === value
                      ? '#2a313a'
                      : '#101419',
                }}
              >
                <Text
                  style={{
                    color:
                      range === value
                        ? '#ffffff'
                        : '#7f8a99',
                    fontWeight: '700',
                    fontSize: 12,
                  }}
                >
                  ±{value}
                </Text>
              </Pressable>
            ))}
          </View>
        </Card>

        <View
          style={{
            backgroundColor: '#15191f',
            borderRadius: 16,
            overflow: 'hidden',
          }}
        >
          <ChainHeader />

          {visibleRows.map((row: any) => (
            <ChainRow
              key={String(row.strike)}
              row={row}
              atm={atm}
              ceWall={Number(market.ceWall)}
              peWall={Number(market.peWall)}
            />
          ))}
        </View>

        <Card>
          <Text style={styles.sectionTitle}>
            ATM DETAIL
          </Text>

          <AtmDetail
            row={chain.find(
              (row: any) =>
                Number(row.strike) === atm,
            )}
          />
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

function ChainHeader() {
  return (
    <View
      style={{
        flexDirection: 'row',
        paddingVertical: 9,
        paddingHorizontal: 8,
        backgroundColor: '#101419',
      }}
    >
      <Text style={[styles.headerCell, { flex: 1.25 }]}>
        CALL OI
      </Text>

      <Text style={[styles.headerCell, { flex: 1 }]}>
        ΔOI
      </Text>

      <Text style={[styles.headerCell, { flex: 0.9 }]}>
        LTP
      </Text>

      <Text
        style={[
          styles.headerCell,
          {
            flex: 1,
            textAlign: 'center',
          },
        ]}
      >
        STRIKE
      </Text>

      <Text
        style={[
          styles.headerCell,
          {
            flex: 0.9,
            textAlign: 'right',
          },
        ]}
      >
        LTP
      </Text>

      <Text
        style={[
          styles.headerCell,
          {
            flex: 1,
            textAlign: 'right',
          },
        ]}
      >
        ΔOI
      </Text>

      <Text
        style={[
          styles.headerCell,
          {
            flex: 1.25,
            textAlign: 'right',
          },
        ]}
      >
        PUT OI
      </Text>
    </View>
  );
}

function ChainRow({
  row,
  atm,
  ceWall,
  peWall,
}: {
  row: any;
  atm: number;
  ceWall: number;
  peWall: number;
}) {
  const strike = Number(row.strike);
  const isAtm = strike === atm;
  const isCeWall = strike === ceWall;
  const isPeWall = strike === peWall;

  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        paddingVertical: 10,
        paddingHorizontal: 8,
        borderTopWidth: 1,
        borderTopColor: '#222831',
        backgroundColor: isAtm
          ? '#202731'
          : '#15191f',
      }}
    >
      <Text
        style={[
          styles.chainCell,
          {
            flex: 1.25,
            color: '#ef7777',
          },
        ]}
      >
        {compactNumber(row.ceOI)}
      </Text>

      <Text
        style={[
          styles.chainCell,
          {
            flex: 1,
            color: changeColor(row.ceChgOI),
          },
        ]}
      >
        {signedCompact(row.ceChgOI)}
      </Text>

      <Text
        style={[
          styles.chainCell,
          {
            flex: 0.9,
          },
        ]}
      >
        {formatNumber(row.ceLTP, 1)}
      </Text>

      <View
        style={{
          flex: 1,
          alignItems: 'center',
        }}
      >
        <Text
          style={{
            color: '#ffffff',
            fontWeight: isAtm ? '900' : '700',
            fontSize: isAtm ? 14 : 12,
          }}
        >
          {isAtm ? '▶ ' : ''}
          {strike}
          {isAtm ? ' ◀' : ''}
        </Text>

        {(isCeWall || isPeWall) && (
          <Text
            style={{
              color: '#9ba5b4',
              fontSize: 8,
              marginTop: 2,
              fontWeight: '700',
            }}
          >
            {isCeWall && isPeWall
              ? 'CE + PE WALL'
              : isCeWall
                ? 'CE WALL'
                : 'PE WALL'}
          </Text>
        )}
      </View>

      <Text
        style={[
          styles.chainCell,
          {
            flex: 0.9,
            textAlign: 'right',
          },
        ]}
      >
        {formatNumber(row.peLTP, 1)}
      </Text>

      <Text
        style={[
          styles.chainCell,
          {
            flex: 1,
            textAlign: 'right',
            color: changeColor(row.peChgOI),
          },
        ]}
      >
        {signedCompact(row.peChgOI)}
      </Text>

      <Text
        style={[
          styles.chainCell,
          {
            flex: 1.25,
            textAlign: 'right',
            color: '#63d297',
          },
        ]}
      >
        {compactNumber(row.peOI)}
      </Text>
    </View>
  );
}

function AtmDetail({ row }: { row: any }) {
  if (!row) {
    return (
      <Text
        style={{
          color: '#7f8a99',
          marginTop: 10,
        }}
      >
        ATM row unavailable.
      </Text>
    );
  }

  return (
    <View style={{ marginTop: 10, gap: 10 }}>
      <View style={styles.detailRow}>
        <DetailMetric
          label="CE IV"
          value={formatNumber(row.ceIV, 2)}
        />

        <DetailMetric
          label="PE IV"
          value={formatNumber(row.peIV, 2)}
        />

        <DetailMetric
          label="FOOTPRINT"
          value={formatNumber(row.footprintScore, 1)}
        />
      </View>

      <View style={styles.detailRow}>
        <DetailMetric
          label="CE SIGNAL"
          value={row.ceSignal || '—'}
        />

        <DetailMetric
          label="PE SIGNAL"
          value={row.peSignal || '—'}
        />
      </View>

      <View style={styles.detailRow}>
        <DetailMetric
          label="CE CAPITAL"
          value={signedCompact(row.ceCapitalFlow)}
        />

        <DetailMetric
          label="PE CAPITAL"
          value={signedCompact(row.peCapitalFlow)}
        />
      </View>
    </View>
  );
}

function SummaryChip({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <View
      style={{
        flex: 1,
        backgroundColor: '#15191f',
        borderRadius: 12,
        padding: 11,
      }}
    >
      <Text style={styles.sectionTitle}>
        {label}
      </Text>

      <Text
        style={{
          color: '#ffffff',
          fontSize: 15,
          fontWeight: '800',
          marginTop: 4,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function DetailMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <View
      style={{
        flex: 1,
        backgroundColor: '#101419',
        borderRadius: 10,
        padding: 10,
      }}
    >
      <Text style={styles.sectionTitle}>
        {label}
      </Text>

      <Text
        numberOfLines={2}
        style={{
          color: '#ffffff',
          fontSize: 12,
          fontWeight: '700',
          marginTop: 5,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <View
      style={{
        backgroundColor: '#15191f',
        borderRadius: 16,
        padding: 14,
      }}
    >
      {children}
    </View>
  );
}

const styles = {
  sectionTitle: {
    color: '#7f8a99',
    fontSize: 10,
    fontWeight: '700' as const,
    letterSpacing: 0.7,
  },

  headerCell: {
    color: '#7f8a99',
    fontSize: 8,
    fontWeight: '800' as const,
  },

  chainCell: {
    color: '#d7dde5',
    fontSize: 10,
    fontWeight: '600' as const,
  },

  detailRow: {
    flexDirection: 'row' as const,
    gap: 8,
  },
};

function formatNumber(
  value: any,
  decimals = 2,
): string {
  const n = Number(value);

  if (!Number.isFinite(n)) return '—';

  return n.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function compactNumber(value: any): string {
  const n = Number(value);

  if (!Number.isFinite(n)) return '—';

  const abs = Math.abs(n);

  if (abs >= 10000000)
    return `${(n / 10000000).toFixed(2)}Cr`;

  if (abs >= 100000)
    return `${(n / 100000).toFixed(1)}L`;

  if (abs >= 1000)
    return `${(n / 1000).toFixed(1)}K`;

  return n.toFixed(0);
}

function signedCompact(value: any): string {
  const n = Number(value);

  if (!Number.isFinite(n)) return '—';

  if (n === 0) return '0';

  return `${n > 0 ? '+' : '-'}${compactNumber(
    Math.abs(n),
  )}`;
}

function changeColor(value: any): string {
  const n = Number(value);

  if (!Number.isFinite(n) || n === 0)
    return '#8c96a5';

  return n > 0 ? '#63d297' : '#ef7777';
}
