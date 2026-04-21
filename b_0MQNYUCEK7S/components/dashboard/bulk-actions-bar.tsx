'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Spinner } from '@/components/ui/spinner'
import { CheckCircle2, Upload, Lock, Unlock, Download, X } from 'lucide-react'

interface BulkActionsBarProps {
  selectedCount: number
  onClearSelection: () => void
  onApprove: () => Promise<void>
  onPush: () => Promise<void>
  onLock: () => Promise<void>
  onUnlock: () => Promise<void>
  onExport: () => void
}

export function BulkActionsBar({
  selectedCount,
  onClearSelection,
  onApprove,
  onPush,
  onLock,
  onUnlock,
  onExport,
}: BulkActionsBarProps) {
  const [showPushConfirm, setShowPushConfirm] = useState(false)
  const [isPushing, setIsPushing] = useState(false)
  const [isApproving, setIsApproving] = useState(false)
  const [isLocking, setIsLocking] = useState(false)
  const [isUnlocking, setIsUnlocking] = useState(false)

  if (selectedCount === 0) return null

  const handlePush = async () => {
    setIsPushing(true)
    try {
      await onPush()
    } finally {
      setIsPushing(false)
      setShowPushConfirm(false)
    }
  }

  const handleApprove = async () => {
    setIsApproving(true)
    try {
      await onApprove()
    } finally {
      setIsApproving(false)
    }
  }

  const handleLock = async () => {
    setIsLocking(true)
    try {
      await onLock()
    } finally {
      setIsLocking(false)
    }
  }

  const handleUnlock = async () => {
    setIsUnlocking(true)
    try {
      await onUnlock()
    } finally {
      setIsUnlocking(false)
    }
  }

  return (
    <>
      <div className="bg-card sticky bottom-0 z-10 border-t shadow-lg">
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">
              {selectedCount} row{selectedCount !== 1 ? 's' : ''} selected
            </span>
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onClearSelection}>
              <X className="mr-1 h-3 w-3" />
              Clear
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8"
              onClick={handleApprove}
              disabled={isApproving}
            >
              {isApproving ? (
                <Spinner className="mr-1.5 h-3.5 w-3.5" />
              ) : (
                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
              )}
              Approve
            </Button>

            <Button
              variant="default"
              size="sm"
              className="h-8"
              onClick={() => setShowPushConfirm(true)}
              disabled={isPushing}
            >
              {isPushing ? (
                <Spinner className="mr-1.5 h-3.5 w-3.5" />
              ) : (
                <Upload className="mr-1.5 h-3.5 w-3.5" />
              )}
              Push to Lightspeed
            </Button>

            <div className="bg-border mx-2 h-6 w-px" />

            <Button
              variant="outline"
              size="sm"
              className="h-8"
              onClick={handleLock}
              disabled={isLocking}
            >
              {isLocking ? (
                <Spinner className="mr-1.5 h-3.5 w-3.5" />
              ) : (
                <Lock className="mr-1.5 h-3.5 w-3.5" />
              )}
              Lock
            </Button>

            <Button
              variant="outline"
              size="sm"
              className="h-8"
              onClick={handleUnlock}
              disabled={isUnlocking}
            >
              {isUnlocking ? (
                <Spinner className="mr-1.5 h-3.5 w-3.5" />
              ) : (
                <Unlock className="mr-1.5 h-3.5 w-3.5" />
              )}
              Unlock
            </Button>

            <div className="bg-border mx-2 h-6 w-px" />

            <Button variant="outline" size="sm" className="h-8" onClick={onExport}>
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Export
            </Button>
          </div>
        </div>
      </div>

      <AlertDialog open={showPushConfirm} onOpenChange={setShowPushConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Push to Lightspeed</AlertDialogTitle>
            <AlertDialogDescription>
              You are about to push reorder point and desired inventory updates for{' '}
              <strong>{selectedCount}</strong> SKU × location combination
              {selectedCount !== 1 ? 's' : ''} to Lightspeed R-Series.
              <br />
              <br />
              This action will update the live inventory settings. Are you sure you want to
              continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isPushing}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePush} disabled={isPushing}>
              {isPushing && <Spinner className="mr-2 h-4 w-4" />}
              Push to Lightspeed
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
