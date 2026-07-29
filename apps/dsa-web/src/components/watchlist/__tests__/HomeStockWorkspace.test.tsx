import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { HomeStockWorkspace } from '../HomeStockWorkspace';
import type { HomeWatchlistRow } from '../HomeStockWorkspace';

function renderWorkspace({
  watchlistRows,
  selectedRecordId,
  selectedStockCode,
}: {
  watchlistRows: HomeWatchlistRow[];
  selectedRecordId?: number;
  selectedStockCode?: string;
}) {
  const onHistoryItemClick = vi.fn();
  const onRemoveFromWatchlist = vi.fn().mockResolvedValue(undefined);
  window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');

  render(
    <UiLanguageProvider>
      <HomeStockWorkspace
        activeTab="watchlist"
        onTabChange={vi.fn()}
        watchlistRows={watchlistRows}
        watchlistLoading={false}
        watchlistActioning={false}
        watchlistMessage={null}
        onAddToWatchlist={vi.fn().mockResolvedValue(undefined)}
        onRemoveFromWatchlist={onRemoveFromWatchlist}
        onRefreshWatchlist={vi.fn().mockResolvedValue(undefined)}
        onAnalyzeWatchlist={vi.fn().mockResolvedValue(undefined)}
        isBatchAnalyzing={false}
        batchStatus={null}
        todayItems={[]}
        isLoadingTodayItems={false}
        todayLoadError={false}
        watchlistAnalyzedTodayCount={watchlistRows.filter((row) => row.analyzedToday).length}
        historyItems={[]}
        isLoadingHistory={false}
        selectedStockCode={selectedStockCode}
        selectedRecordId={selectedRecordId}
        onHistoryItemClick={onHistoryItemClick}
      />
    </UiLanguageProvider>,
  );

  return {
    onHistoryItemClick,
    onRemoveFromWatchlist,
  };
}

describe('HomeStockWorkspace', () => {
  it('opens the latest watchlist detail with keyboard interaction and keeps the row selected', () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: '600519',
        analyzedToday: true,
        latestItem: {
          id: 21,
          stockCode: '600519',
          stockName: '贵州茅台',
          sentimentScore: 88,
          operationAdvice: '买入',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+08:00',
        },
      }],
      selectedRecordId: 21,
    });

    const row = screen.getByRole('button', { name: '打开 600519 最新分析详情' });
    fireEvent.keyDown(row, { key: 'Enter' });

    expect(onHistoryItemClick).toHaveBeenCalledWith(21);
    expect(row).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows an explicit notice when a watchlist row has no detail yet', async () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: 'AAPL',
        analyzedToday: false,
      }],
    });

    const row = screen.getByRole('button', { name: '暂无 AAPL 的分析详情，可先分析' });
    fireEvent.keyDown(row, { key: ' ' });

    expect(await screen.findByRole('alert')).toHaveTextContent('暂无分析详情，可先分析。');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('does not bubble delete clicks into detail opening', async () => {
    const { onHistoryItemClick, onRemoveFromWatchlist } = renderWorkspace({
      watchlistRows: [{
        code: '600519',
        analyzedToday: true,
        latestItem: {
          id: 21,
          stockCode: '600519',
          stockName: '贵州茅台',
          sentimentScore: 88,
          operationAdvice: '买入',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+08:00',
        },
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: '从自选股移除 600519' }));

    expect(onRemoveFromWatchlist).toHaveBeenCalledWith('600519');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('keeps the watchlist row selected for equivalent stock-code formats', () => {
    renderWorkspace({
      watchlistRows: [{
        code: 'HK700',
        analyzedToday: true,
        latestItem: {
          id: 88,
          stockCode: '00700',
          stockName: '腾讯控股',
          sentimentScore: 91,
          operationAdvice: '买入',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+08:00',
        },
      }],
      selectedStockCode: '00700.HK',
    });

    expect(screen.getByRole('button', { name: '打开 HK700 最新分析详情' })).toHaveAttribute('aria-pressed', 'true');
  });
});
