/**
 * Contract tests for shared constants in AnalysisBrowser.shared.ts.
 *
 * Pins the set of lesson_status values that FindingCard and
 * AnalysisHistoryView must render. Prior to consolidation the two
 * views had drifted: FindingCard mapped `processing` + `orphaned`,
 * AnalysisHistoryView did not, so those two statuses rendered with
 * an empty class in the history panel.
 */

import { describe, it, expect } from 'vitest'
import { FEEDBACK_STATUS_CLASS } from './AnalysisBrowser.shared'

describe('FEEDBACK_STATUS_CLASS', () => {
  it('maps every lesson_status value the backend can emit', () => {
    const expected = [
      'pending',
      'processing',
      'validated',
      'best_effort',
      'error',
      'orphaned',
    ].sort()
    expect(Object.keys(FEEDBACK_STATUS_CLASS).sort()).toEqual(expected)
  })

  it('assigns a non-empty class to every status', () => {
    for (const [status, className] of Object.entries(FEEDBACK_STATUS_CLASS)) {
      expect(className, `status=${status}`).toBeTruthy()
    }
  })
})
