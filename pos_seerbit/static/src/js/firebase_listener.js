/** @odoo-module **/

import { _t } from '@web/core/l10n/translation';
import { AlertDialog } from '@web/core/confirmation_dialog/confirmation_dialog';
import FirebaseInit from './firebase_init';

// Track active listeners to prevent duplicates
const activeListeners = new Map();

// Listen for payment reconciliation updates
function listenForReconciliation(transactionId, env) {
    // If we already have a listener for this transaction, don't create another one
    if (activeListeners.has(transactionId)) {
        console.log('Listener already exists for transaction:', transactionId);
        return;
    }

    const initializeListener = () => {
        if (!FirebaseInit.isFirebaseAvailable()) {
            console.warn('Firestore not available for reconciliation. Status:', FirebaseInit.getFirebaseStatus());
            FirebaseInit.reinitializeFirebase(window.__owl__.root.env.services.orm).then(function(success) {
                if (success) {
                    initializeListener();
                } else {
                    console.error('Failed to reinitialize Firestore');
                }
            });
            return;
        }

        const firestoreDb = FirebaseInit.getFirestoreDb();
        if (!firestoreDb) {
            console.warn('Firestore database not available for reconciliation');
            return;
        }

        // Query without orderBy to avoid composite index requirement
        const reconciliationsRef = firestoreDb.collection('reconciliations')
            .where('id', '==', transactionId)
            .limit(1);

        const unsubscribe = reconciliationsRef.onSnapshot(
            async (snapshot) => {
                if (snapshot.empty) {
                    console.log('No matching transaction found for ID:', transactionId);
                    return;
                }

                snapshot.docChanges().forEach(async (change) => {
                    if (change.type === 'added') {
                        const data = change.doc.data();
                        const pending = JSON.parse(localStorage.getItem('pending_transaction') || 'null');
                        console.log('Payment reconciliation data received:', data);
                        
                        // Validate the transaction matches our pending one
                        if (pending && data?.id === pending?.id && data?.posid === pending?.posid ) {
                            const status = String(data?.status || '').toLowerCase();
                            if (['success', 'completed', 'complete', 'done', 'successful'].includes(status)) {
                                // Store the completed transaction
                                localStorage.setItem('completed_transaction', JSON.stringify(data));
                                console.log('Payment reconciliation completed successfully');
                                
                                // Clean up the listener after successful processing
                                cleanupListener(transactionId);
                            }
                        }
                    }
                });
            },
            (error) => {
                console.error('Firestore listener error:', error);
                cleanupListener(transactionId);
            }
        );

        // Store the unsubscribe function
        activeListeners.set(transactionId, unsubscribe);
    };

    initializeListener();
}

// Clean up a listener by transaction ID
function cleanupListener(transactionId) {
    const unsubscribe = activeListeners.get(transactionId);
    if (unsubscribe) {
        unsubscribe();
        activeListeners.delete(transactionId);
        console.log('Cleaned up listener for transaction:', transactionId);
    }
}

// Clean up all listeners
function cleanupAllListeners() {
    for (const [id, unsubscribe] of activeListeners.entries()) {
        unsubscribe();
        console.log('Cleaned up listener for transaction:', id);
    }
    activeListeners.clear();
}

// Clean up on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', cleanupAllListeners);
}

export default {
    listenForReconciliation
};