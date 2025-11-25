// firebase-auth.js - CLEANUP: Removed Firebase Storage

// --- 1. Firebase Configuration ---
const firebaseConfig = {
    apiKey: "AIzaSyAY0m9wB5NvAx3XVESQCCrJVFSyQEkF7Qw",
    authDomain: "hushh-63300.firebaseapp.com",
    projectId: "hushh-63300",
    storageBucket: "hushh-63300.firebasestorage.app",
    messagingSenderId: "42737578730",
    appId: "1:42737578730:web:cf808ed1985124971ddd93",
    measurementId: "G-KSH2W0TLPJ"
};

// Initialize Firebase
if (!firebase.apps.length) {
    firebase.initializeApp(firebaseConfig);
}

const auth = firebase.auth();
const db = firebase.firestore();

// --- 2. Constants & Variables ---
const PROFILE_COLLECTION = 'users';
const PROFILE_SETUP_PATH = '/profile-setup';
const VIDEO_CHAT_PATH = '/video-chat';

// --- 3. Authentication Functions ---

function signInWithGoogle() {
    const provider = new firebase.auth.GoogleAuthProvider();
    auth.signInWithPopup(provider)
        .then((result) => {
            console.log("Google Sign-in successful. Checking profile status...");
        })
        .catch((error) => {
            console.error("Google Sign-in Error:", error.code, error.message);
            alert(`Sign-in failed: ${error.message}`);
        });
}

async function getUserProfile(uid) {
    try {
        const doc = await db.collection(PROFILE_COLLECTION).doc(uid).get();
        if (doc.exists) {
            const data = doc.data();
            if (data.age && data.gender && data.interests && data.interests.length > 0) {
                window.profile = data; 
                return data;
            }
        }
        return null;
    } catch (error) {
        console.error("Error fetching user profile:", error);
        return null;
    }
}

async function createOrUpdateUserProfile(uid, profileData) {
    try {
        const userRef = db.collection(PROFILE_COLLECTION).doc(uid);
        await userRef.set({
            ...profileData,
            uid: uid,
            photoURL: auth.currentUser.photoURL || null, 
            email: auth.currentUser.email || null,
            createdAt: firebase.firestore.FieldValue.serverTimestamp(),
            lastUpdated: firebase.firestore.FieldValue.serverTimestamp()
        }, { merge: true });
        console.log("User profile updated successfully!");
    } catch (error) {
        console.error("Error saving user profile:", error);
        throw error;
    }
}


// --- 4. Page-Specific Auth Handlers ---
async function handleVideoPageAuth(user) {
    const currentPath = window.location.pathname;
    
    if (currentPath !== VIDEO_CHAT_PATH) {
        return;
    }
    
    if (!user) {
        window.location.href = '/';
        return;
    }
    
    const profile = await getUserProfile(user.uid);
    if (!profile) {
        window.location.href = PROFILE_SETUP_PATH;
        return;
    }
    
    console.log('Video chat access granted');
}

async function handleProfilePageAuth(user) {
    const currentPath = window.location.pathname;
    
    if (currentPath !== PROFILE_SETUP_PATH) {
        return;
    }
    
    if (!user) {
        window.location.href = '/';
        return;
    }
    
    console.log('Profile setup access granted');
}

// --- 5. Auth Listener Setup (PAGE-SPECIFIC) ---
document.addEventListener('DOMContentLoaded', () => {
    const currentPath = window.location.pathname;
    
    auth.onAuthStateChanged(user => {
        if (currentPath === VIDEO_CHAT_PATH) {
            handleVideoPageAuth(user);
        }
        else if (currentPath === PROFILE_SETUP_PATH) {
            handleProfilePageAuth(user);
        }
    });
    
    const signInBtn = document.getElementById('signInBtn');
    if (signInBtn) {
        signInBtn.addEventListener('click', signInWithGoogle);
    }
});

// Export functions for use in other scripts
window.signInWithGoogle = signInWithGoogle;
window.createOrUpdateUserProfile = createOrUpdateUserProfile;
window.getUserProfile = getUserProfile;
window.firebaseAuth = auth;
window.firebaseDB = db;