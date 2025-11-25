// video-chat-logic.js - FIXED: ICE candidates, auto-search, control positioning

// ============================================
// GLOBAL STATE & DOM ELEMENTS
// ============================================
let socket = null;
let firebaseUser = null;
let localStream = null;
let remoteStream = null;
let peerConnection = null;
let currentRoom = null;
let isTimedDate = false;
let promptTimerInterval = null;
let callTimerInterval = null;
let callStartTime = null;
let autoReconnect = true;
let pendingIceCandidates = []; 

const TIMED_DATE_DURATION = 90; // seconds

// Configuration
const ICE_SERVERS = {
    iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' },
        { urls: 'stun:stun2.l.google.com:19302' },
        { urls: 'stun:stun3.l.google.com:19302' },
        { urls: 'stun:stun4.l.google.com:19302' }
    ],
    iceCandidatePoolSize: 10
};

// DOM Elements
const videoContainer = document.getElementById('videoContainer');
const searchScreen = document.getElementById('searchScreen');
const callStatusEl = document.getElementById('callStatus');
const callTimer = document.getElementById('callTimer');
const remoteVideo = document.getElementById('remoteVideo');
const localVideo = document.getElementById('localVideo');
const remotePlaceholder = document.getElementById('remotePlaceholder');
const remoteAvatarMobile = document.getElementById('remoteAvatarMobile');
const remoteNameEl = document.getElementById('remoteName');
const connectionInfo = document.getElementById('connectionInfo');
const promptDisplay = document.getElementById('prompt-display');
const promptTextEl = document.getElementById('prompt-text');
const timerDisplay = document.getElementById('timer-display');
const decisionButtons = document.getElementById('decisionButtons');
const continueChatBtn = document.getElementById('continueChatBtn');
const endDateBtn = document.getElementById('endDateBtn');
const skipBtn = document.getElementById('skipBtn');
const endCallBtn = document.getElementById('endCallBtn');
const startCallBtn = document.getElementById('startCallBtn');
const flipCameraBtn = document.getElementById('flipCameraBtn');
const callControls = document.getElementById('callControls');
const remoteUserAvatar = document.getElementById('remoteUserAvatar');
const remoteUserAvatarImg = document.getElementById('remoteUserAvatarImg');
const verifiedBadgeEl = document.getElementById('verifiedBadge'); // NEW: Badge element

// ============================================
// INITIALIZATION
// ============================================
firebaseAuth.onAuthStateChanged(async (user) => {
    if (user) {
        firebaseUser = user;
        const profile = await getUserProfile(user.uid);
        
        if (profile) {
             profile.photoURL = user.photoURL; 
             await initializeMedia();
             initializeSocket(user.uid, profile);
             
             // AUTO-START SEARCH IMMEDIATELY - Wait 500ms for socket connection
             setTimeout(() => {
                 if (socket && socket.connected) {
                     console.log('Auto-starting search...');
                     startSearch();
                 } else {
                     // Retry if socket not ready
                     setTimeout(() => {
                         if (socket && socket.connected) {
                             console.log('Auto-starting search (retry)...');
                             startSearch();
                         }
                     }, 1000);
                 }
             }, 500);
        } else {
            window.location.href = '/profile-setup';
        }
    } else {
        window.location.href = '/';
    }
});

// ============================================
// MEDIA STREAM MANAGEMENT
// ============================================
async function initializeMedia(facingMode = 'user') {
    try {
        const constraints = {
            video: {
                facingMode: facingMode,
                width: { ideal: 1280 },
                height: { ideal: 720 }
            },
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        };

        if (localStream) {
            localStream.getTracks().forEach(track => track.stop());
        }

        localStream = await navigator.mediaDevices.getUserMedia(constraints);
        localVideo.srcObject = localStream;
        currentFacingMode = facingMode;

        console.log('Local media stream initialized successfully');
    } catch (error) {
        console.error('Error accessing media devices:', error);
        alert('Unable to access camera/microphone. Please check permissions.');
    }
}

// ============================================
// SOCKET.IO SETUP
// ============================================
function initializeSocket(uid, profile) {
    socket = io({
        query: { 
            firebase_uid: uid,
            profile: JSON.stringify(profile)
        },
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 5
    });

    socket.on('connect', () => {
        console.log('Socket connected:', socket.id);
        setVideoState('idle');
        continueChatBtn.disabled = false;
        endDateBtn.disabled = false;
    });

    socket.on('disconnect', () => {
        console.log('Socket disconnected');
        cleanup(false);
    });

    socket.on('video-matched', handleVideoMatched);
    socket.on('video-waiting', handleVideoWaiting);
    socket.on('video-user-disconnected', handleUserDisconnected);
    
    socket.on('video-offer', handleVideoOffer);
    socket.on('video-answer', handleVideoAnswer);
    socket.on('ice-candidate', handleIceCandidate);
    
    socket.on('start_timed_date', handleStartTimedDate);
    socket.on('match_decision_received', handleMatchDecisionReceived);
    socket.on('paired_match', handlePairedMatch);

    socket.on('banned', (data) => {
        alert(`You have been banned: ${data.message}`);
        window.location.href = '/';
    });
}

// ============================================
// WEBRTC PEER CONNECTION (Unchanged)
// ============================================
function createPeerConnection() {
    try {
        peerConnection = new RTCPeerConnection(ICE_SERVERS);
        pendingIceCandidates = []; 

        if (localStream) {
            localStream.getTracks().forEach(track => {
                peerConnection.addTrack(track, localStream);
            });
        }

        peerConnection.ontrack = (event) => {
            console.log('Received remote track', event.track.kind);
            if (!remoteStream) {
                remoteStream = new MediaStream();
                remoteVideo.srcObject = remoteStream;
            }
            if (!remoteStream.getTrackById(event.track.id)) {
                remoteStream.addTrack(event.track);
            }
            
            if (event.track.kind === 'video') {
                 remotePlaceholder.style.display = 'none';
            }
        };

        peerConnection.onicecandidate = (event) => {
            if (event.candidate && currentRoom) {
                socket.emit('ice-candidate', {
                    room: currentRoom,
                    candidate: event.candidate
                });
            }
        };

        peerConnection.onconnectionstatechange = () => {
            console.log('Connection state:', peerConnection.connectionState);
            
            if (peerConnection.connectionState === 'connected') {
                startCallTimer();
                processPendingIceCandidates();
            } else if (peerConnection.connectionState === 'disconnected') {
                console.log('Connection disconnected, attempting reconnection...');
            } else if (peerConnection.connectionState === 'failed') {
                console.error('Connection failed');
                handleUserDisconnected();
            }
        };

        peerConnection.oniceconnectionstatechange = () => {
            console.log('ICE connection state:', peerConnection.iceConnectionState);
            
            if (peerConnection.iceConnectionState === 'failed') {
                peerConnection.restartIce();
            }
        };

        console.log('Peer connection created successfully');
        return peerConnection;

    } catch (error) {
        console.error('Error creating peer connection:', error);
        return null;
    }
}

// ============================================
// WEBRTC SIGNALING HANDLERS
// ============================================
async function handleVideoMatched(data) {
    console.log('Video matched:', data);
    
    currentRoom = data.room;
    setVideoState('connecting');
    
    remoteNameEl.textContent = data.remote_name || 'Stranger';
    
    // UPDATED: Handle Verification Badge
    if (data.remote_verified) {
        verifiedBadgeEl.style.display = 'inline';
        remoteNameEl.textContent = `${data.remote_name || 'Stranger'}`; // Update text content without badge
    } else {
        verifiedBadgeEl.style.display = 'none';
    }

    if (data.remote_photo) {
        remoteAvatarMobile.style.backgroundImage = `url('${data.remote_photo}')`;
        remoteAvatarMobile.classList.add('has-photo');
        
        remoteUserAvatarImg.src = data.remote_photo;
        remoteUserAvatar.classList.add('has-photo');
        remoteUserAvatar.style.display = 'block';
    } else {
        remoteAvatarMobile.style.backgroundImage = 'none';
        remoteAvatarMobile.classList.remove('has-photo');
        
        remoteUserAvatar.style.display = 'none';
        remoteUserAvatar.classList.remove('has-photo');
    }

    connectionInfo.textContent = data.shared_interests ? 
        `Shared interests: ${data.shared_interests}` : 'Establishing connection...';

    if (!peerConnection) {
        createPeerConnection();
    }

    if (data.initiator) {
        try {
            const offer = await peerConnection.createOffer({
                offerToReceiveAudio: true,
                offerToReceiveVideo: true
            });
            
            await peerConnection.setLocalDescription(offer);
            
            socket.emit('video-offer', {
                room: currentRoom,
                offer: offer
            });
            
            console.log('Offer sent');
        } catch (error) {
            console.error('Error creating offer:', error);
        }
    }
}

async function handleVideoOffer(data) {
    console.log('Received offer');
    
    try {
        if (!peerConnection) {
            createPeerConnection();
        }

        await peerConnection.setRemoteDescription(new RTCSessionDescription(data.offer));
        
        processPendingIceCandidates();
        
        const answer = await peerConnection.createAnswer();
        await peerConnection.setLocalDescription(answer);
        
        socket.emit('video-answer', {
            room: currentRoom,
            answer: answer
        });
        
        console.log('Answer sent');
    } catch (error) {
        console.error('Error handling offer:', error);
    }
}

async function handleVideoAnswer(data) {
    console.log('Received answer');
    
    try {
        await peerConnection.setRemoteDescription(new RTCSessionDescription(data.answer));
        
        processPendingIceCandidates();
    } catch (error) {
        console.error('Error handling answer:', error);
    }
}

async function handleIceCandidate(data) {
    console.log('Received ICE candidate');
    
    try {
        if (!peerConnection) {
            console.warn('Peer connection not ready, queueing ICE candidate');
            pendingIceCandidates.push(data.candidate);
            return;
        }

        if (!peerConnection.remoteDescription || !peerConnection.remoteDescription.type) {
            console.warn('Remote description not set, queueing ICE candidate');
            pendingIceCandidates.push(data.candidate);
            return;
        }

        if (data.candidate) {
            await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
            console.log('ICE candidate added successfully');
        }
    } catch (error) {
        console.error('Error adding ICE candidate:', error);
        pendingIceCandidates.push(data.candidate);
    }
}

async function processPendingIceCandidates() {
    if (pendingIceCandidates.length === 0) return;
    
    console.log(`Processing ${pendingIceCandidates.length} pending ICE candidates`);
    
    const candidates = [...pendingIceCandidates];
    pendingIceCandidates = [];
    
    for (const candidate of candidates) {
        try {
            if (peerConnection && peerConnection.remoteDescription) {
                await peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
                console.log('Queued ICE candidate added');
            }
        } catch (error) {
            console.error('Error adding queued ICE candidate:', error);
        }
    }
}

function handleVideoWaiting() {
    setVideoState('searching');
    callStatusEl.textContent = 'Searching for match...';
    connectionInfo.textContent = 'Finding someone based on your interests';
    verifiedBadgeEl.style.display = 'none'; 
}

function handleUserDisconnected() {
    console.log('User disconnected');
    
    clearInterval(promptTimerInterval);
    promptTimerInterval = null;
    decisionButtons.style.display = 'none';
    promptDisplay.style.opacity = '0';
    promptDisplay.style.display = 'none';
    callTimer.classList.remove('decision');
    
    if (remoteUserAvatar) {
        remoteUserAvatar.style.display = 'none';
    }
    
    verifiedBadgeEl.style.display = 'none'; 
    
    cleanup(autoReconnect);
}

// ============================================
// TIMED DATE LOGIC (Unchanged)
// ============================================
function handleStartTimedDate(data) {
    console.log('Starting 90-second timed date');
    
    isTimedDate = true;
    const promptText = data.prompt;
    
    promptTextEl.textContent = promptText;
    promptDisplay.style.display = 'flex';
    setTimeout(() => { promptDisplay.style.opacity = '1'; }, 100);
    
    setVideoState('active');
    callStatusEl.textContent = 'Date Active (90s)';
    
    skipBtn.disabled = true;
    continueChatBtn.disabled = false;
    endDateBtn.disabled = false;
    decisionButtons.style.display = 'none';
    
    let timeLeft = TIMED_DATE_DURATION;
    timerDisplay.textContent = `00:${timeLeft.toString().padStart(2, '0')}`;
    callTimer.textContent = `01:30`;
    
    promptTimerInterval = setInterval(() => {
        timeLeft--;
        const minutes = Math.floor(timeLeft / 60);
        const seconds = timeLeft % 60;
        const formattedTime = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        
        callTimer.textContent = formattedTime;
        timerDisplay.textContent = formattedTime;

        if (timeLeft <= 0) {
            clearInterval(promptTimerInterval);
            promptTimerInterval = null;
            handleTimedDateEnd();
        }
    }, 1000);
}

function handleTimedDateEnd() {
    console.log('90-second timer ended');
    
    isTimedDate = false;
    skipBtn.disabled = false;
    
    promptDisplay.style.opacity = '0';
    setTimeout(() => { promptDisplay.style.display = 'none'; }, 500);
    
    decisionButtons.style.display = 'flex';
    callStatusEl.textContent = 'Decision Time!';
    callTimer.classList.add('decision');
}

function sendDecision(action) {
    if (isTimedDate) {
        alert('Wait until the 90-second period ends');
        return;
    }
    
    if (!currentRoom) return;

    console.log(`Sending decision: ${action}`);
    
    continueChatBtn.disabled = true;
    endDateBtn.disabled = true;
    
    callStatusEl.textContent = action === 'continue' ? 
        'Waiting for partner...' : 'Ending date...';
    
    socket.emit('match_decision', { 
        action: action, 
        room: currentRoom 
    });
    
    if (action === 'end') {
         handleUserDisconnected();
    }
}

function handleMatchDecisionReceived(data) {
    console.log(`Partner's decision: ${data.action}`);
}

function handlePairedMatch() {
    console.log('Successful match!');
    
    decisionButtons.style.display = 'none';
    callTimer.classList.remove('decision');

    callStatusEl.textContent = 'Matched! Unlimited Chat';
    
    skipBtn.disabled = false;
    endCallBtn.disabled = false;
}

// ============================================
// UI STATE MANAGEMENT (Unchanged)
// ============================================
function setVideoState(state) {
    videoContainer.className = 'video-container';
    
    if (state === 'searching') {
        searchScreen.style.display = 'flex';
        callStatusEl.textContent = 'Finding your date...';
        connectionInfo.textContent = 'Searching for a match';
    } else if (state === 'connecting') {
        searchScreen.style.display = 'none';
        callStatusEl.textContent = 'Connecting...';
        connectionInfo.textContent = 'Establishing connection...';
    } else if (state === 'active') {
        searchScreen.style.display = 'none';
        remotePlaceholder.style.display = 'none';
    } else {
        searchScreen.style.display = 'none';
        remotePlaceholder.style.display = 'flex';
    }
    
    videoContainer.classList.add(state);
}

function startCallTimer() {
    if (callTimerInterval) clearInterval(callTimerInterval);
    callStartTime = Date.now();
    
    callTimerInterval = setInterval(() => {
        if (!isTimedDate) { 
            const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;
            callTimer.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
    }, 1000);
}

function stopCallTimer() {
    if (callTimerInterval) {
        clearInterval(callTimerInterval);
        callTimerInterval = null;
    }
    callTimer.textContent = '00:00';
}

// ============================================
// CLEANUP & RECONNECTION (Unchanged)
// ============================================
function cleanup(shouldReconnect = false) {
    console.log('Cleaning up resources...');
    
    clearInterval(promptTimerInterval);
    clearInterval(callTimerInterval);
    promptTimerInterval = null;
    callTimerInterval = null;
    
    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }
    
    if (remoteStream) {
        remoteStream.getTracks().forEach(track => track.stop());
        remoteStream = null;
    }
    
    remoteVideo.srcObject = null;
    remotePlaceholder.style.display = 'flex';
    
    remoteAvatarMobile.style.backgroundImage = 'none';
    remoteAvatarMobile.classList.remove('has-photo');
    remoteNameEl.textContent = 'Match';
    connectionInfo.textContent = 'Start your 90-second date';
    
    if (remoteUserAvatar) {
        remoteUserAvatar.style.display = 'none';
        remoteUserAvatar.classList.remove('has-photo');
    }
    
    verifiedBadgeEl.style.display = 'none'; 
    
    currentRoom = null;
    isTimedDate = false;
    pendingIceCandidates = [];
    
    decisionButtons.style.display = 'none';
    promptDisplay.style.opacity = '0';
    promptDisplay.style.display = 'none';
    callTimer.classList.remove('decision');
    
    stopCallTimer();
    
    if (shouldReconnect && socket && socket.connected) {
        console.log('Auto-reconnecting...');
        setVideoState('searching');
        socket.emit('find-video-match');
    } else {
        setVideoState('idle');
    }
}

// ============================================
// START SEARCH FUNCTION (Unchanged)
// ============================================
function startSearch() {
    if (socket && socket.connected) {
        autoReconnect = true; 
        setVideoState('searching');
        socket.emit('find-video-match');
    } else {
        alert('Connection not ready. Please refresh the page.');
    }
}

// ============================================
// EVENT HANDLERS (Unchanged)
// ============================================
startCallBtn.onclick = startSearch;

skipBtn.onclick = () => {
    if (isTimedDate) {
        alert('Complete the 90-second prompt before skipping');
        return;
    }
    
    if (decisionButtons.style.display === 'flex') {
        sendDecision('end');
    } else {
        cleanup(true);
    }
};

endCallBtn.onclick = () => {
    autoReconnect = false;
    if (currentRoom) {
         sendDecision('end');
    } else {
         cleanup(false);
         window.location.href = '/';
    }
};

continueChatBtn.onclick = () => sendDecision('continue');
endDateBtn.onclick = () => sendDecision('end');

// ============================================
// CLEANUP ON PAGE UNLOAD (Unchanged)
// ============================================
window.addEventListener('beforeunload', () => {
    if (localStream) {
        localStream.getTracks().forEach(track => track.stop());
    }
    cleanup(false);
});