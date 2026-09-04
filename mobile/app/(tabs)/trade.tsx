import { ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

export default function TradeScreen() {
  const { payload, connected, error } = useMarketData();

  const trade: any = payload?.trade ?? {};
  const portfolio: any = trade.portfolio ?? {};
  const orders: any[] = Array.isArray(trade.orders) ? trade.orders : [];
  const supervision: any = trade.supervision ?? {};
  const positions: any[] = Array.isArray(portfolio.positions)
    ? portfolio.positions
    : [];

  const mode = String(trade.mode || 'PAPER');
  const liveEnabled = Boolean(trade.liveTradingEnabled);
  const killSwitch = Boolean(trade.killSwitchActive);

  return (
    <SafeAreaView
      style={{ flex: 1, backgroundColor: '#0b0d10' }}
      edges={['top']}
    >
      <MarketContextBar />

      <ScrollView
        contentContainerStyle={{
          padding: 14,
          paddingBottom: 50,
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
            <Text style={styles.pageTitle}>Trade</Text>
            <Text style={styles.subtitle}>
              Portfolio, orders & execution safety
            </Text>
          </View>

          <Text
            style={{
              color: connected ? '#63d297' : '#ef7777',
              fontSize: 12,
              fontWeight: '800',
            }}
          >
            {connected ? '● LIVE' : '● OFFLINE'}
          </Text>
        </View>

        {error ? (
          <Card title="CONNECTION">
            <Text style={{ color: '#ef7777' }}>{error}</Text>
          </Card>
        ) : null}

        <Card title="TRADING MODE">
          <View
            style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <View>
              <Text
                style={{
                  color: mode === 'LIVE' ? '#ef7777' : '#63d297',
                  fontSize: 25,
                  fontWeight: '900',
                }}
              >
                {mode}
              </Text>

              <Text style={styles.muted}>
                Mobile remains read-only
              </Text>
            </View>

            <View
              style={{
                backgroundColor:
                  mode === 'LIVE' ? '#351719' : '#173025',
                paddingHorizontal: 12,
                paddingVertical: 7,
                borderRadius: 999,
              }}
            >
              <Text
                style={{
                  color: mode === 'LIVE' ? '#ef7777' : '#63d297',
                  fontWeight: '800',
                  fontSize: 11,
                }}
              >
                {mode === 'LIVE' ? 'REAL MONEY' : 'SIMULATION'}
              </Text>
            </View>
          </View>

          <StatusRow
            label="Execution broker"
            value={String(trade.executionBroker || '—')}
          />

          <StatusRow
            label="Live trading"
            value={liveEnabled ? 'ENABLED' : 'DISABLED'}
            color={liveEnabled ? '#ef7777' : '#63d297'}
          />

          <StatusRow
            label="Kill switch"
            value={killSwitch ? 'ACTIVE' : 'CLEAR'}
            color={killSwitch ? '#ef7777' : '#63d297'}
          />
        </Card>

        <Card title="P&L">
          <View style={styles.grid}>
            <Metric
              label="EQUITY"
              value={money(portfolio.equity)}
            />

            <Metric
              label="CAPITAL"
              value={money(portfolio.capital)}
            />

            <Metric
              label="REALIZED"
              value={signedMoney(portfolio.realized_pnl)}
              tone={pnlTone(portfolio.realized_pnl)}
            />

            <Metric
              label="UNREALIZED"
              value={signedMoney(portfolio.unrealized_pnl)}
              tone={pnlTone(portfolio.unrealized_pnl)}
            />

            <Metric
              label="TOTAL P&L"
              value={signedMoney(portfolio.total_pnl ?? portfolio.pnl)}
              tone={pnlTone(portfolio.total_pnl ?? portfolio.pnl)}
            />

            <Metric
              label="POSITIONS"
              value={String(positions.length)}
            />
          </View>
        </Card>

        <Card title="POSITIONS">
          {positions.length ? (
            positions.map((p: any, index: number) => (
              <PositionRow
                key={String(
                  p.id ??
                    p.instrument_key ??
                    p.symbol ??
                    index,
                )}
                position={p}
              />
            ))
          ) : (
            <Empty text="No open paper positions." />
          )}
        </Card>

        <Card title="ORDERS">
          {orders.length ? (
            orders
              .slice()
              .reverse()
              .slice(0, 20)
              .map((o: any, index: number) => (
                <OrderRow
                  key={String(o.id ?? o.order_id ?? index)}
                  order={o}
                />
              ))
          ) : (
            <Empty text="No paper orders yet." />
          )}
        </Card>

        <Card title="ACCOUNT SAFETY">
          <StatusRow
            label="Open lots"
            value={formatAny(
              supervision?.accountGuard?.current_open_lots,
            )}
          />

          <StatusRow
            label="Max lots / order"
            value={formatAny(supervision.maxLotsPerOrder)}
          />

          <StatusRow
            label="Max orders / minute"
            value={formatAny(supervision.maxOrdersPerMinute)}
          />

          <StatusRow
            label="Account guard"
            value={guardText(supervision.accountGuard)}
            color={
              guardTone(supervision.accountGuard) === 'negative'
                ? '#ef7777'
                : '#63d297'
            }
          />
        </Card>

        <Card title="AUTO EXECUTOR">
          <StatusRow
            label="Status"
            value={objectText(supervision.autoExecutor)}
          />

          <StatusRow
            label="Symbol"
            value={String(supervision.symbol || '—')}
          />
        </Card>

        <Card title="BROKER READINESS">
          {Array.isArray(payload?.market?.dataSources) ? (
            payload.market.dataSources.map((broker: any) => (
              <StatusRow
                key={String(broker.id)}
                label={String(broker.label || broker.id)}
                value={
                  broker.active
                    ? `${broker.status} • ACTIVE`
                    : String(broker.status || '—')
                }
                color={
                  broker.ready
                    ? '#63d297'
                    : broker.status === 'POLLING'
                      ? '#f2c96d'
                      : '#ef7777'
                }
              />
            ))
          ) : (
            <Empty text="Broker status unavailable." />
          )}
        </Card>

        <View
          style={{
            backgroundColor: '#101419',
            borderRadius: 14,
            padding: 14,
          }}
        >
          <Text
            style={{
              color: '#7f8a99',
              fontSize: 11,
              lineHeight: 17,
            }}
          >
            This mobile endpoint is presentation-only. It cannot place,
            cancel, modify or route orders.
          </Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function PositionRow({ position }: { position: any }) {
  const pnl =
    position.unrealized_pnl ??
    position.pnl ??
    position.mtm;

  const qty =
    position.net_qty_lots ??
    position.qty_lots ??
    position.quantity ??
    position.netqty ??
    0;

  const symbol =
    position.tradingsymbol ??
    position.symbol ??
    position.instrument ??
    position.instrument_key ??
    'POSITION';

  return (
    <View style={styles.row}>
      <View style={{ flex: 1 }}>
        <Text
          numberOfLines={1}
          style={{
            color: '#ffffff',
            fontSize: 13,
            fontWeight: '800',
          }}
        >
          {symbol}
        </Text>

        <Text style={styles.muted}>
          Qty {formatAny(qty)}
          {position.average_price != null
            ? `  •  Avg ${formatNumber(position.average_price, 2)}`
            : ''}
          {position.last_price != null
            ? `  •  LTP ${formatNumber(position.last_price, 2)}`
            : ''}
        </Text>
      </View>

      <Text
        style={{
          color: pnlColor(pnl),
          fontWeight: '800',
          fontSize: 13,
          marginLeft: 10,
        }}
      >
        {signedMoney(pnl)}
      </Text>
    </View>
  );
}

function OrderRow({ order }: { order: any }) {
  const side = String(order.side || order.transactiontype || '—').toUpperCase();

  const status = String(
    order.status ??
      order.orderstatus ??
      '—',
  ).toUpperCase();

  const symbol =
    order.tradingsymbol ??
    order.symbol ??
    order.instrument ??
    'ORDER';

  const qty =
    order.qty_lots ??
    order.quantity ??
    order.qty ??
    order.filledshares;

  const price =
    order.fill_price ??
    order.averageprice ??
    order.limit_price ??
    order.price;

  return (
    <View style={styles.row}>
      <View
        style={{
          width: 44,
          marginRight: 8,
        }}
      >
        <Text
          style={{
            color: side === 'BUY' ? '#63d297' : '#ef7777',
            fontSize: 11,
            fontWeight: '900',
          }}
        >
          {side}
        </Text>
      </View>

      <View style={{ flex: 1 }}>
        <Text
          numberOfLines={1}
          style={{
            color: '#ffffff',
            fontSize: 12,
            fontWeight: '700',
          }}
        >
          {symbol}
        </Text>

        <Text style={styles.muted}>
          {qty != null ? `Qty ${qty}` : ''}
          {price != null ? `  •  ${formatNumber(price, 2)}` : ''}
        </Text>
      </View>

      <Text
        style={{
          color: orderStatusColor(status),
          fontSize: 10,
          fontWeight: '800',
          marginLeft: 8,
        }}
      >
        {status}
      </Text>
    </View>
  );
}

function Metric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'positive' | 'negative' | 'neutral';
}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>

      <Text
        style={{
          color:
            tone === 'positive'
              ? '#63d297'
              : tone === 'negative'
                ? '#ef7777'
                : '#ffffff',
          fontSize: 16,
          fontWeight: '800',
          marginTop: 5,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function StatusRow({
  label,
  value,
  color = '#ffffff',
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={styles.row}>
      <Text
        style={{
          color: '#9ba5b4',
          fontSize: 12,
          flex: 1,
        }}
      >
        {label}
      </Text>

      <Text
        numberOfLines={2}
        style={{
          color,
          fontSize: 11,
          fontWeight: '800',
          textAlign: 'right',
          maxWidth: '58%',
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <Text
      style={{
        color: '#7f8a99',
        fontSize: 12,
        paddingVertical: 13,
      }}
    >
      {text}
    </Text>
  );
}

const styles = {
  pageTitle: {
    color: '#ffffff',
    fontSize: 25,
    fontWeight: '800' as const,
  },

  subtitle: {
    color: '#7f8a99',
    fontSize: 12,
    marginTop: 4,
  },

  sectionTitle: {
    color: '#7f8a99',
    fontSize: 10,
    fontWeight: '800' as const,
    letterSpacing: 0.8,
    marginBottom: 8,
  },

  card: {
    backgroundColor: '#15191f',
    borderRadius: 16,
    padding: 15,
  },

  row: {
    flexDirection: 'row' as const,
    justifyContent: 'space-between' as const,
    alignItems: 'center' as const,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: '#232830',
  },

  grid: {
    flexDirection: 'row' as const,
    flexWrap: 'wrap' as const,
    justifyContent: 'space-between' as const,
    gap: 9,
    marginTop: 4,
  },

  metric: {
    width: '48%' as const,
    backgroundColor: '#101419',
    borderRadius: 12,
    padding: 12,
  },

  metricLabel: {
    color: '#7f8a99',
    fontSize: 9,
    fontWeight: '700' as const,
    letterSpacing: 0.4,
  },

  muted: {
    color: '#7f8a99',
    fontSize: 10,
    marginTop: 4,
  },
};

function number(value: any): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  return `₹${Math.abs(n).toLocaleString('en-IN', {
    maximumFractionDigits: 2,
  })}`;
}

function signedMoney(value: any): string {
  const n = number(value);

  if (n === null) return '—';
  if (n === 0) return '₹0';

  return `${n > 0 ? '+' : '-'}${money(n)}`;
}

function formatNumber(value: any, decimals = 2): string {
  const n = number(value);

  if (n === null) return '—';

  return n.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function formatAny(value: any): string {
  if (value === null || value === undefined) return '—';
  return String(value);
}

function pnlTone(value: any): 'positive' | 'negative' | 'neutral' {
  const n = number(value);

  if (n === null || n === 0) return 'neutral';
  return n > 0 ? 'positive' : 'negative';
}

function pnlColor(value: any): string {
  const tone = pnlTone(value);

  if (tone === 'positive') return '#63d297';
  if (tone === 'negative') return '#ef7777';
  return '#ffffff';
}

function orderStatusColor(status: string): string {
  const s = status.toUpperCase();

  if (
    s.includes('FILLED') ||
    s.includes('COMPLETE') ||
    s.includes('EXECUTED')
  ) {
    return '#63d297';
  }

  if (
    s.includes('REJECT') ||
    s.includes('CANCEL')
  ) {
    return '#ef7777';
  }

  return '#f2c96d';
}

function guardText(value: any): string {
  if (!value || typeof value !== 'object') return '—';

  if (value.is_tripped || value.tripped) return 'TRIPPED';

  if (value.reason) return String(value.reason);

  return 'OK';
}

function guardTone(value: any): 'positive' | 'negative' {
  if (!value || typeof value !== 'object') return 'positive';

  return value.is_tripped || value.tripped ? 'negative' : 'positive';
}

function objectText(value: any): string {
  if (value === null || value === undefined) return '—';

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value);
  }

  if (typeof value === 'object') {
    const keys = [
      'status',
      'enabled',
      'mode',
      'reason',
      'signal',
      'state',
    ];

    const parts = keys
      .filter(
        key =>
          value[key] !== undefined &&
          value[key] !== null &&
          typeof value[key] !== 'object',
      )
      .map(key => `${key}: ${value[key]}`);

    if (parts.length) return parts.join(' • ');

    const simple = Object.entries(value)
      .filter(
        ([, v]) =>
          typeof v === 'string' ||
          typeof v === 'number' ||
          typeof v === 'boolean',
      )
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${v}`);

    return simple.length ? simple.join(' • ') : 'Available';
  }

  return '—';
}
