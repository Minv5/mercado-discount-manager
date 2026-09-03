import assert from 'node:assert/strict';
import test from 'node:test';

import { businessReasonText, toChineseError } from '../src/errors.js';
import { summarizeExecutionGroup } from '../src/executionGroupPersistence.js';

test('technical result codes use the approved Chinese business wording', () => {
  assert.equal(
    toChineseError({ code: 'PROMOTION_ITEMS_UNREADABLE', details: { results: null } }),
    '平台未返回可读取的商品清单，暂无法确认取消结果。',
  );
  assert.equal(businessReasonText('pending_relations_present'), '存在待平台确认的商品关系。');
  assert.equal(businessReasonText('accounting_complete=false'), '本次结果尚未全部确认。');
  assert.equal(
    businessReasonText('原因：PROMOTION_ITEMS_UNREADABLE'),
    '原因：平台未返回可读取的商品清单，暂无法确认取消结果。',
  );
});

test('group summaries retain technical completeness reasons for diagnostics', () => {
  const summary = summarizeExecutionGroup({
    action: 'cancel',
    children: [{
      job_id: 'job-technical-reason',
      account_id: 'account-A',
      status: 'failed',
      incomplete_reasons: ['pending_relations_present'],
      result: {
        accounting_complete: false,
        execution: {
          relation_count: 2,
          total: 2,
          success: 1,
          failed: 0,
          skipped: 0,
          pending: 1,
          accounting_complete: false,
          terminal_counts: {
            relation_count: 2,
            success: 1,
            failed: 0,
            skipped: 0,
            platform_pending: 0,
            unresolved: 1,
            classified_count: 2,
            is_closed: false,
            is_resolved: false,
          },
        },
      },
    }],
  });

  assert.equal(summary.accounting_complete, false);
  assert.ok(summary.incomplete_reasons.includes('pending_relations_present'));
  assert.equal(businessReasonText(summary.incomplete_reasons[0]), '存在待平台确认的商品关系。');
});
