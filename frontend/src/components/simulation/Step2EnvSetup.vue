<template>
  <div class="env-setup-panel">
    <div class="scroll-container">
      <!-- Step 01: Simulation Instance -->
      <div class="step-card" :class="{ 'active': phase === 0, 'completed': phase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">Simulation Instance Initialization</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 0" class="badge success">Completed</span>
            <span v-else class="badge processing">Initializing</span>
          </div>
        </div>
        
        <div class="card-content">
          <p class="api-note">POST /api/simulation/create</p>
          <p class="description">
            Create simulation instance and fetch simulation world parameter template
          </p>

          <div v-if="simulationId" class="info-card">
            <div class="info-row">
              <span class="info-label">Project ID</span>
              <span class="info-value mono">{{ projectData?.project_id }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Graph ID</span>
              <span class="info-value mono">{{ projectData?.graph_id }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Simulation ID</span>
              <span class="info-value mono">{{ simulationId }}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Task ID</span>
              <span class="info-value mono">{{ taskId || 'Async task completed' }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 02: Generate Agent Profiles -->
      <div class="step-card" :class="{ 'active': phase === 1, 'completed': phase > 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">Generate Agent Profiles</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 1" class="badge success">Completed</span>
            <span v-else-if="phase === 1" class="badge processing">{{ prepareProgress }}%</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/prepare</p>
          <p class="description">
            Combine context, automatically call tools to extract entities and relationships from knowledge graph, initialize simulation individuals, and give them unique behaviors and memories based on reality seeds
          </p>

          <!-- Profiles Stats -->
          <div v-if="profiles.length > 0" class="stats-grid">
            <div class="stat-card">
              <span class="stat-value">{{ profiles.length }}</span>
              <span class="stat-label">Current Agents</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ expectedTotal || '-' }}</span>
              <span class="stat-label">Expected Total Agents</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ totalTopicsCount }}</span>
              <span class="stat-label">Reality Seed Associated Topics</span>
            </div>
          </div>

          <!-- Profiles List Preview -->
          <div v-if="profiles.length > 0" class="profiles-preview">
            <div class="preview-header">
              <span class="preview-title">Generated Agent Profiles</span>
            </div>
            <div class="profiles-list">
              <div 
                v-for="(profile, idx) in profiles" 
                :key="idx" 
                class="profile-card"
                @click="selectProfile(profile)"
              >
                <div class="profile-header">
                  <span class="profile-realname">{{ profile.username || 'Unknown' }}</span>
                  <span class="profile-username">@{{ profile.name || `agent_${idx}` }}</span>
                </div>
                <div class="profile-meta">
                  <span class="profile-profession">{{ profile.profession || 'Unknown Profession' }}</span>
                </div>
                <p class="profile-bio">{{ profile.bio || 'No bio available' }}</p>
                <div v-if="profile.interested_topics?.length" class="profile-topics">
                  <span 
                    v-for="topic in profile.interested_topics.slice(0, 3)" 
                    :key="topic" 
                    class="topic-tag"
                  >{{ topic }}</span>
                  <span v-if="profile.interested_topics.length > 3" class="topic-more">
                    +{{ profile.interested_topics.length - 3 }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 03: Generate Dual-Platform Simulation Config -->
      <div class="step-card" :class="{ 'active': phase === 2, 'completed': phase > 2 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">03</span>
            <span class="step-title">Generate Dual-Platform Config</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 2" class="badge success">Completed</span>
            <span v-else-if="phase === 2" class="badge processing">Generating</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/prepare</p>
          <p class="description">
            LLM intelligently sets world time flow, recommendation algorithms, active time periods for each individual, posting frequency, event triggers and other parameters based on simulation requirements and reality seeds
          </p>
          
          <!-- Config Preview -->
          <div v-if="simulationConfig" class="config-detail-panel">
            <!-- Time Configuration -->
            <div class="config-block">
              <div class="config-grid">
                <div class="config-item">
                  <span class="config-item-label">Simulation Duration</span>
                  <span class="config-item-value">{{ simulationConfig.time_config?.total_simulation_hours || '-' }} hours</span>
                </div>
                <div class="config-item">
                  <span class="config-item-label">Per Round Duration</span>
                  <span class="config-item-value">{{ simulationConfig.time_config?.minutes_per_round || '-' }} minutes</span>
                </div>
                <div class="config-item">
                  <span class="config-item-label">Total Rounds</span>
                  <span class="config-item-value">{{ Math.floor((simulationConfig.time_config?.total_simulation_hours * 60 / simulationConfig.time_config?.minutes_per_round)) || '-' }} rounds</span>
                </div>
                <div class="config-item">
                  <span class="config-item-label">Active Per Hour</span>
                  <span class="config-item-value">{{ simulationConfig.time_config?.agents_per_hour_min }}-{{ simulationConfig.time_config?.agents_per_hour_max }}</span>
                </div>
              </div>
              <div class="time-periods">
                <div class="period-item">
                  <span class="period-label">Peak Hours</span>
                  <span class="period-hours">{{ simulationConfig.time_config?.peak_hours?.join(':00, ') }}:00</span>
                  <span class="period-multiplier">×{{ simulationConfig.time_config?.peak_activity_multiplier }}</span>
                </div>
                <div class="period-item">
                  <span class="period-label">Work Hours</span>
                  <span class="period-hours">{{ simulationConfig.time_config?.work_hours?.[0] }}:00-{{ simulationConfig.time_config?.work_hours?.slice(-1)[0] }}:00</span>
                  <span class="period-multiplier">×{{ simulationConfig.time_config?.work_activity_multiplier }}</span>
                </div>
                <div class="period-item">
                  <span class="period-label">Morning Hours</span>
                  <span class="period-hours">{{ simulationConfig.time_config?.morning_hours?.[0] }}:00-{{ simulationConfig.time_config?.morning_hours?.slice(-1)[0] }}:00</span>
                  <span class="period-multiplier">×{{ simulationConfig.time_config?.morning_activity_multiplier }}</span>
                </div>
                <div class="period-item">
                  <span class="period-label">Off-Peak Hours</span>
                  <span class="period-hours">{{ simulationConfig.time_config?.off_peak_hours?.[0] }}:00-{{ simulationConfig.time_config?.off_peak_hours?.slice(-1)[0] }}:00</span>
                  <span class="period-multiplier">×{{ simulationConfig.time_config?.off_peak_activity_multiplier }}</span>
                </div>
              </div>
            </div>

            <!-- Agent Configuration -->
            <div class="config-block">
              <div class="config-block-header">
                <span class="config-block-title">Agent Configuration</span>
                <span class="config-block-badge">{{ simulationConfig.agent_configs?.length || 0 }} agents</span>
              </div>
              <div class="agents-cards">
                <div 
                  v-for="agent in simulationConfig.agent_configs" 
                  :key="agent.agent_id" 
                  class="agent-card"
                >
                  <!-- Card Header -->
                  <div class="agent-card-header">
                    <div class="agent-identity">
                      <span class="agent-id">Agent {{ agent.agent_id }}</span>
                      <span class="agent-name">{{ agent.entity_name }}</span>
                    </div>
                    <div class="agent-tags">
                      <span class="agent-type">{{ agent.entity_type }}</span>
                      <span class="agent-stance" :class="'stance-' + agent.stance">{{ agent.stance }}</span>
                    </div>
                  </div>
                  
                  <!-- Active Timeline -->
                  <div class="agent-timeline">
                    <span class="timeline-label">Active Hours</span>
                    <div class="mini-timeline">
                      <div 
                        v-for="hour in 24" 
                        :key="hour - 1" 
                        class="timeline-hour"
                        :class="{ 'active': agent.active_hours?.includes(hour - 1) }"
                        :title="`${hour - 1}:00`"
                      ></div>
                    </div>
                    <div class="timeline-marks">
                      <span>0</span>
                      <span>6</span>
                      <span>12</span>
                      <span>18</span>
                      <span>24</span>
                    </div>
                  </div>

                  <!-- Behavior Parameters -->
                  <div class="agent-params">
                    <div class="param-group">
                      <div class="param-item">
                        <span class="param-label">Posts/Hour</span>
                        <span class="param-value">{{ agent.posts_per_hour }}</span>
                      </div>
                      <div class="param-item">
                        <span class="param-label">Comments/Hour</span>
                        <span class="param-value">{{ agent.comments_per_hour }}</span>
                      </div>
                      <div class="param-item">
                        <span class="param-label">Response Delay</span>
                        <span class="param-value">{{ agent.response_delay_min }}-{{ agent.response_delay_max }}min</span>
                      </div>
                    </div>
                    <div class="param-group">
                      <div class="param-item">
                        <span class="param-label">Activity Level</span>
                        <span class="param-value with-bar">
                          <span class="mini-bar" :style="{ width: (agent.activity_level * 100) + '%' }"></span>
                          {{ (agent.activity_level * 100).toFixed(0) }}%
                        </span>
                      </div>
                      <div class="param-item">
                        <span class="param-label">Sentiment Bias</span>
                        <span class="param-value" :class="agent.sentiment_bias > 0 ? 'positive' : agent.sentiment_bias < 0 ? 'negative' : 'neutral'">
                          {{ agent.sentiment_bias > 0 ? '+' : '' }}{{ agent.sentiment_bias?.toFixed(1) }}
                        </span>
                      </div>
                      <div class="param-item">
                        <span class="param-label">Influence</span>
                        <span class="param-value highlight">{{ agent.influence_weight?.toFixed(1) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Platform Configuration -->
            <div class="config-block">
              <div class="config-block-header">
                <span class="config-block-title">Recommendation Algorithm Config</span>
              </div>
              <div class="platforms-grid">
                <div v-if="simulationConfig.twitter_config" class="platform-card">
                  <div class="platform-card-header">
                    <span class="platform-name">Platform 1: Plaza / Feed</span>
                  </div>
                  <div class="platform-params">
                    <div class="param-row">
                      <span class="param-label">Recency Weight</span>
                      <span class="param-value">{{ simulationConfig.twitter_config.recency_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Popularity Weight</span>
                      <span class="param-value">{{ simulationConfig.twitter_config.popularity_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Relevance Weight</span>
                      <span class="param-value">{{ simulationConfig.twitter_config.relevance_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Viral Threshold</span>
                      <span class="param-value">{{ simulationConfig.twitter_config.viral_threshold }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Echo Chamber Strength</span>
                      <span class="param-value">{{ simulationConfig.twitter_config.echo_chamber_strength }}</span>
                    </div>
                  </div>
                </div>
                <div v-if="simulationConfig.reddit_config" class="platform-card">
                  <div class="platform-card-header">
                    <span class="platform-name">Platform 2: Topic / Community</span>
                  </div>
                  <div class="platform-params">
                    <div class="param-row">
                      <span class="param-label">Recency Weight</span>
                      <span class="param-value">{{ simulationConfig.reddit_config.recency_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Popularity Weight</span>
                      <span class="param-value">{{ simulationConfig.reddit_config.popularity_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Relevance Weight</span>
                      <span class="param-value">{{ simulationConfig.reddit_config.relevance_weight }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Viral Threshold</span>
                      <span class="param-value">{{ simulationConfig.reddit_config.viral_threshold }}</span>
                    </div>
                    <div class="param-row">
                      <span class="param-label">Echo Chamber Strength</span>
                      <span class="param-value">{{ simulationConfig.reddit_config.echo_chamber_strength }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- LLM Configuration Reasoning -->
            <div v-if="simulationConfig.generation_reasoning" class="config-block">
              <div class="config-block-header">
                <span class="config-block-title">LLM Configuration Reasoning</span>
              </div>
              <div class="reasoning-content">
                <div 
                  v-for="(reason, idx) in simulationConfig.generation_reasoning.split('|').slice(0, 2)" 
                  :key="idx" 
                  class="reasoning-item"
                >
                  <p class="reasoning-text">{{ reason.trim() }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 04: Initial Activation Orchestration -->
      <div class="step-card" :class="{ 'active': phase === 3, 'completed': phase > 3 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">04</span>
            <span class="step-title">Initial Activation Orchestration</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 3" class="badge success">Completed</span>
            <span v-else-if="phase === 3" class="badge processing">Orchestrating</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/prepare</p>
          <p class="description">
            Based on narrative direction, automatically generate initial activation events and hot topics to guide the initial state of the simulation world
          </p>

          <div v-if="simulationConfig?.event_config" class="orchestration-content">
            <!-- Narrative Direction -->
            <div class="narrative-box">
              <span class="box-label narrative-label">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class="special-icon">
                  <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" stroke="url(#paint0_linear)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                  <path d="M16.24 7.76L14.12 14.12L7.76 16.24L9.88 9.88L16.24 7.76Z" fill="url(#paint0_linear)" stroke="url(#paint0_linear)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                  <defs>
                    <linearGradient id="paint0_linear" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
                      <stop stop-color="#FF5722"/>
                      <stop offset="1" stop-color="#FF9800"/>
                    </linearGradient>
                  </defs>
                </svg>
                Narrative Guidance Direction
              </span>
              <p class="narrative-text">{{ simulationConfig.event_config.narrative_direction }}</p>
            </div>

            <!-- Hot Topics -->
            <div class="topics-section">
              <span class="box-label">Initial Hot Topics</span>
              <div class="hot-topics-grid">
                <span v-for="topic in simulationConfig.event_config.hot_topics" :key="topic" class="hot-topic-tag">
                  # {{ topic }}
                </span>
              </div>
            </div>

            <!-- Initial Post Stream -->
            <div class="initial-posts-section">
              <span class="box-label">Initial Activation Sequence ({{ simulationConfig.event_config.initial_posts.length }})</span>
              <div class="posts-timeline">
                <div v-for="(post, idx) in simulationConfig.event_config.initial_posts" :key="idx" class="timeline-item">
                  <div class="timeline-marker"></div>
                  <div class="timeline-content">
                    <div class="post-header">
                      <span class="post-role">{{ post.poster_type }}</span>
                      <span class="post-agent-info">
                        <span class="post-id">Agent {{ post.poster_agent_id }}</span>
                        <span class="post-username">@{{ getAgentUsername(post.poster_agent_id) }}</span>
                      </span>
                    </div>
                    <p class="post-text">{{ post.content }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 05: Preparation Complete -->
      <div class="step-card" :class="{ 'active': phase === 4 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">05</span>
            <span class="step-title">Preparation Complete</span>
          </div>
          <div class="step-status">
            <span v-if="phase >= 4" class="badge processing">In Progress</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/start</p>
          <p class="description">Simulation environment is ready, you can start running the simulation</p>
          
          <!-- Simulation rounds config - only show after config generation is complete and rounds are calculated -->
          <div v-if="simulationConfig && autoGeneratedRounds" class="rounds-config-section">
            <div class="rounds-header">
              <div class="header-left">
                <span class="section-title">Simulation Rounds Setting</span>
                <span class="section-desc">Vega automatically plans to simulate <span class="desc-highlight">{{ simulationConfig?.time_config?.total_simulation_hours || '-' }}</span> hours of reality, each round represents <span class="desc-highlight">{{ simulationConfig?.time_config?.minutes_per_round || '-' }}</span> minutes of real time</span>
              </div>
              <label class="switch-control">
                <input type="checkbox" v-model="useCustomRounds">
                <span class="switch-track"></span>
                <span class="switch-label">Custom</span>
              </label>
            </div>
            
            <Transition name="fade" mode="out-in">
              <div v-if="useCustomRounds" class="rounds-content custom" key="custom">
                <div class="slider-display">
                  <div class="slider-main-value">
                    <span class="val-num">{{ customMaxRounds }}</span>
                    <span class="val-unit">rounds</span>
                  </div>
                  <div class="slider-meta-info">
                    <span>For 100 agents: Estimated time ~{{ Math.round(customMaxRounds * 0.6) }} minutes</span>
                  </div>
                </div>

                <div class="range-wrapper">
                  <input 
                    type="range" 
                    v-model.number="customMaxRounds" 
                    min="10" 
                    :max="autoGeneratedRounds"
                    step="5"
                    class="minimal-slider"
                    :style="{ '--percent': ((customMaxRounds - 10) / (autoGeneratedRounds - 10)) * 100 + '%' }"
                  />
                  <div class="range-marks">
                    <span>10</span>
                    <span 
                      class="mark-recommend" 
                      :class="{ active: customMaxRounds === 40 }"
                      @click="customMaxRounds = 40"
                      :style="{ position: 'absolute', left: `calc(${(40 - 10) / (autoGeneratedRounds - 10) * 100}% - 30px)` }"
                    >40 (Recommended)</span>
                    <span>{{ autoGeneratedRounds }}</span>
                  </div>
                </div>
              </div>
              
              <div v-else class="rounds-content auto" key="auto">
                <div class="auto-info-card">
                  <div class="auto-value">
                    <span class="val-num">{{ autoGeneratedRounds }}</span>
                    <span class="val-unit">rounds</span>
                  </div>
                  <div class="auto-content">
                    <div class="auto-meta-row">
                      <span class="duration-badge">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                          <circle cx="12" cy="12" r="10"></circle>
                          <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        For 100 agents: Estimated time {{ Math.round(autoGeneratedRounds * 0.6) }} minutes
                      </span>
                    </div>
                    <div class="auto-desc">
                      <p class="highlight-tip" @click="useCustomRounds = true">For first run, strongly recommend switching to 'Custom mode' to reduce simulation rounds for quick preview and lower error risk ➝</p>
                    </div>
                  </div>
                </div>
              </div>
            </Transition>
          </div>

          <div class="action-group dual">
            <button 
              class="action-btn secondary"
              @click="$emit('go-back')"
            >
              ← Back to Graph Build
            </button>
            <button 
              class="action-btn primary"
              :disabled="phase < 4"
              @click="handleStartSimulation"
            >
              Start Dual-World Parallel Simulation ➝
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Profile Detail Modal -->
    <Transition name="modal">
      <div v-if="selectedProfile" class="profile-modal-overlay" @click.self="selectedProfile = null">
        <div class="profile-modal">
          <div class="modal-header">
          <div class="modal-header-info">
            <div class="modal-name-row">
              <span class="modal-realname">{{ selectedProfile.username }}</span>
              <span class="modal-username">@{{ selectedProfile.name }}</span>
            </div>
            <span class="modal-profession">{{ selectedProfile.profession }}</span>
          </div>
          <button class="close-btn" @click="selectedProfile = null">×</button>
        </div>
        
        <div class="modal-body">
          <!-- Basic Information -->
          <div class="modal-info-grid">
            <div class="info-item">
              <span class="info-label">Age</span>
              <span class="info-value">{{ selectedProfile.age || '-' }} years</span>
            </div>
            <div class="info-item">
              <span class="info-label">Gender</span>
              <span class="info-value">{{ { male: 'Male', female: 'Female', other: 'Other' }[selectedProfile.gender] || selectedProfile.gender }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Country/Region</span>
              <span class="info-value">{{ selectedProfile.country || '-' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">MBTI</span>
              <span class="info-value mbti">{{ selectedProfile.mbti || '-' }}</span>
            </div>
          </div>

          <!-- Bio -->
          <div class="modal-section">
            <span class="section-label">Profile Bio</span>
            <p class="section-bio">{{ selectedProfile.bio || 'No bio available' }}</p>
          </div>

          <!-- Interested Topics -->
          <div class="modal-section" v-if="selectedProfile.interested_topics?.length">
            <span class="section-label">Reality Seed Associated Topics</span>
            <div class="topics-grid">
              <span 
                v-for="topic in selectedProfile.interested_topics" 
                :key="topic" 
                class="topic-item"
              >{{ topic }}</span>
            </div>
          </div>

          <!-- Detailed Persona -->
          <div class="modal-section" v-if="selectedProfile.persona">
            <span class="section-label">Detailed Persona Background</span>
            
            <!-- Persona Dimensions Overview -->
            <div class="persona-dimensions">
              <div class="dimension-card">
                <span class="dim-title">Event Experience</span>
                <span class="dim-desc">Complete behavioral trajectory in this event</span>
              </div>
              <div class="dimension-card">
                <span class="dim-title">Behavior Pattern</span>
                <span class="dim-desc">Experience summary and behavioral style preferences</span>
              </div>
              <div class="dimension-card">
                <span class="dim-title">Unique Memory</span>
                <span class="dim-desc">Memories formed based on reality seeds</span>
              </div>
              <div class="dimension-card">
                <span class="dim-title">Social Network</span>
                <span class="dim-desc">Individual connections and interaction graph</span>
              </div>
            </div>

            <div class="persona-content">
              <p class="section-persona">{{ selectedProfile.persona }}</p>
            </div>
          </div>
        </div>
      </div>
      </div>
    </Transition>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">SYSTEM DASHBOARD</span>
        <span class="log-id">{{ simulationId || 'NO_SIMULATION' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { 
  prepareSimulation, 
  getPrepareStatus, 
  getSimulationProfilesRealtime,
  getSimulationConfig,
  getSimulationConfigRealtime 
} from '../../api/simulation'

const props = defineProps({
  simulationId: String,  // Passed from parent component
  projectData: Object,
  graphData: Object,
  systemLogs: Array
})

const emit = defineEmits(['go-back', 'next-step', 'add-log', 'update-status'])

// State
const phase = ref(0) // 0: Initialization, 1: Generate profiles, 2: Generate config, 3: Complete
const taskId = ref(null)
const prepareProgress = ref(0)
const currentStage = ref('')
const progressMessage = ref('')
const profiles = ref([])
const entityTypes = ref([])
const expectedTotal = ref(null)
const simulationConfig = ref(null)
const selectedProfile = ref(null)
const showProfilesDetail = ref(true)

// Log deduplication: Record last output key information
let lastLoggedMessage = ''
let lastLoggedProfileCount = 0
let lastLoggedConfigStage = ''

// Simulation rounds configuration
const useCustomRounds = ref(false) // Default to auto-configured rounds
const customMaxRounds = ref(40)   // Default recommended 40 rounds

// Watch stage to update phase
watch(currentStage, (newStage) => {
  if (newStage === 'Generate Agent Profiles' || newStage === 'generating_profiles') {
    phase.value = 1
  } else if (newStage === 'Generate Simulation Config' || newStage === 'generating_config') {
    phase.value = 2
    // Enter config generation phase, start polling config
    if (!configTimer) {
      addLog('Starting dual-platform simulation config generation...')
      startConfigPolling()
    }
  } else if (newStage === 'Prepare Simulation Scripts' || newStage === 'copying_scripts') {
    phase.value = 2 // Still in config phase
  }
})

// Calculate auto-generated rounds from config (not using hardcoded default)
const autoGeneratedRounds = computed(() => {
  if (!simulationConfig.value?.time_config) {
    return null // Return null when config not generated
  }
  const totalHours = simulationConfig.value.time_config.total_simulation_hours
  const minutesPerRound = simulationConfig.value.time_config.minutes_per_round
  if (!totalHours || !minutesPerRound) {
    return null // Return null when config data incomplete
  }
  const calculatedRounds = Math.floor((totalHours * 60) / minutesPerRound)
  // Ensure max rounds not less than 40 (recommended), avoid slider range issues
  return Math.max(calculatedRounds, 40)
})

// Polling timer
let pollTimer = null
let profilesTimer = null
let configTimer = null

// Computed
const displayProfiles = computed(() => {
  if (showProfilesDetail.value) {
    return profiles.value
  }
  return profiles.value.slice(0, 6)
})

// Get username by agent_id
const getAgentUsername = (agentId) => {
  if (profiles.value && profiles.value.length > agentId && agentId >= 0) {
    const profile = profiles.value[agentId]
    return profile?.username || `agent_${agentId}`
  }
  return `agent_${agentId}`
}

// Calculate total associated topics count for all profiles
const totalTopicsCount = computed(() => {
  return profiles.value.reduce((sum, p) => {
    return sum + (p.interested_topics?.length || 0)
  }, 0)
})

// Methods
const addLog = (msg) => {
  emit('add-log', msg)
}

// Handle start simulation button click
const handleStartSimulation = () => {
  // Build parameters to pass to parent component
  const params = {}
  
  if (useCustomRounds.value) {
    // User custom rounds, pass max_rounds parameter
    params.maxRounds = customMaxRounds.value
    addLog(`Starting simulation, custom rounds: ${customMaxRounds.value} rounds`)
  } else {
    // User keeps auto-generated rounds, don't pass max_rounds parameter
    addLog(`Starting simulation, using auto-configured rounds: ${autoGeneratedRounds.value} rounds`)
  }
  
  emit('next-step', params)
}

const truncateBio = (bio) => {
  if (bio.length > 80) {
    return bio.substring(0, 80) + '...'
  }
  return bio
}

const selectProfile = (profile) => {
  selectedProfile.value = profile
}

// Auto start prepare simulation
const startPrepareSimulation = async () => {
  if (!props.simulationId) {
    addLog('Error: Missing simulationId')
    emit('update-status', 'error')
    return
  }
  
  // Mark first step complete, start second step
  phase.value = 1
  addLog(`Simulation instance created: ${props.simulationId}`)
  addLog('Preparing simulation environment...')
  emit('update-status', 'processing')
  
  try {
    const res = await prepareSimulation({
      simulation_id: props.simulationId,
      use_llm_for_profiles: true,
      parallel_profile_count: 5
    })
    
    if (res.success && res.data) {
      if (res.data.already_prepared) {
        addLog('Detected existing completed preparation, using directly')
        await loadPreparedData()
        return
      }
      
      taskId.value = res.data.task_id
      addLog(`Preparation task started`)
      addLog(`  └─ Task ID: ${res.data.task_id}`)
      
      // Immediately set expected total agents (from prepare API response)
      if (res.data.expected_entities_count) {
        expectedTotal.value = res.data.expected_entities_count
        addLog(`Read ${res.data.expected_entities_count} entities from Zep graph`)
        if (res.data.entity_types && res.data.entity_types.length > 0) {
          addLog(`  └─ Entity types: ${res.data.entity_types.join(', ')}`)
        }
      }
      
      addLog('Starting to poll preparation progress...')
      // Start polling progress
      startPolling()
      // Start real-time fetching of Profiles
      startProfilesPolling()
    } else {
      addLog(`Preparation failed: ${res.error || 'Unknown error'}`)
      emit('update-status', 'error')
    }
  } catch (err) {
    addLog(`Preparation error: ${err.message}`)
    emit('update-status', 'error')
  }
}

const startPolling = () => {
  pollTimer = setInterval(pollPrepareStatus, 2000)
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const startProfilesPolling = () => {
  profilesTimer = setInterval(fetchProfilesRealtime, 3000)
}

const stopProfilesPolling = () => {
  if (profilesTimer) {
    clearInterval(profilesTimer)
    profilesTimer = null
  }
}

const pollPrepareStatus = async () => {
  if (!taskId.value && !props.simulationId) return
  
  try {
    const res = await getPrepareStatus({
      task_id: taskId.value,
      simulation_id: props.simulationId
    })
    
    if (res.success && res.data) {
      const data = res.data
      
      // Update progress
      prepareProgress.value = data.progress || 0
      progressMessage.value = data.message || ''
      
      // Parse stage info and output detailed logs
      if (data.progress_detail) {
        currentStage.value = data.progress_detail.current_stage_name || ''
        
        // Output detailed progress logs (avoid duplicates)
        const detail = data.progress_detail
        const logKey = `${detail.current_stage}-${detail.current_item}-${detail.total_items}`
        if (logKey !== lastLoggedMessage && detail.item_description) {
          lastLoggedMessage = logKey
          const stageInfo = `[${detail.stage_index}/${detail.total_stages}]`
          if (detail.total_items > 0) {
            addLog(`${stageInfo} ${detail.current_stage_name}: ${detail.current_item}/${detail.total_items} - ${detail.item_description}`)
          } else {
            addLog(`${stageInfo} ${detail.current_stage_name}: ${detail.item_description}`)
          }
        }
      } else if (data.message) {
        // Extract stage from message
        const match = data.message.match(/\[(\d+)\/(\d+)\]\s*([^:]+)/)
        if (match) {
          currentStage.value = match[3].trim()
        }
        // Output message logs (avoid duplicates)
        if (data.message !== lastLoggedMessage) {
          lastLoggedMessage = data.message
          addLog(data.message)
        }
      }
      
      // Check if completed
      if (data.status === 'completed' || data.status === 'ready' || data.already_prepared) {
        addLog('✓ Preparation work completed')
        stopPolling()
        stopProfilesPolling()
        await loadPreparedData()
      } else if (data.status === 'failed') {
        addLog(`✗ Preparation failed: ${data.error || 'Unknown error'}`)
        stopPolling()
        stopProfilesPolling()
      }
    }
  } catch (err) {
    console.warn('Polling status failed:', err)
  }
}

const fetchProfilesRealtime = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getSimulationProfilesRealtime(props.simulationId, 'reddit')
    
    if (res.success && res.data) {
      const prevCount = profiles.value.length
      profiles.value = res.data.profiles || []
      // Only update when API returns valid value, avoid overwriting existing valid values
      if (res.data.total_expected) {
        expectedTotal.value = res.data.total_expected
      }
      
      // Extract entity types
      const types = new Set()
      profiles.value.forEach(p => {
        if (p.entity_type) types.add(p.entity_type)
      })
      entityTypes.value = Array.from(types)
      
      // Output Profile generation progress logs (only when count changes)
      const currentCount = profiles.value.length
      if (currentCount > 0 && currentCount !== lastLoggedProfileCount) {
        lastLoggedProfileCount = currentCount
        const total = expectedTotal.value || '?'
        const latestProfile = profiles.value[currentCount - 1]
        const profileName = latestProfile?.name || latestProfile?.username || `Agent_${currentCount}`
        if (currentCount === 1) {
          addLog(`Starting to generate Agent profiles...`)
        }
        addLog(`→ Agent profile ${currentCount}/${total}: ${profileName} (${latestProfile?.profession || 'Unknown Profession'})`)
        
        // If all generation completed
        if (expectedTotal.value && currentCount >= expectedTotal.value) {
          addLog(`✓ All ${currentCount} Agent profiles generated`)
        }
      }
    }
  } catch (err) {
    console.warn('Failed to fetch Profiles:', err)
  }
}

// Config polling
const startConfigPolling = () => {
  configTimer = setInterval(fetchConfigRealtime, 2000)
}

const stopConfigPolling = () => {
  if (configTimer) {
    clearInterval(configTimer)
    configTimer = null
  }
}

const fetchConfigRealtime = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getSimulationConfigRealtime(props.simulationId)
    
    if (res.success && res.data) {
      const data = res.data
      
      // Output config generation stage logs (avoid duplicates)
      if (data.generation_stage && data.generation_stage !== lastLoggedConfigStage) {
        lastLoggedConfigStage = data.generation_stage
        if (data.generation_stage === 'generating_profiles') {
          addLog('Generating Agent profile configuration...')
        } else if (data.generation_stage === 'generating_config') {
          addLog('Calling LLM to generate simulation config parameters...')
        }
      }
      
      // If config already generated
      if (data.config_generated && data.config) {
        simulationConfig.value = data.config
        addLog('✓ Simulation config generation completed')
        
        // Show detailed config summary
        if (data.summary) {
          addLog(`  ├─ Agent count: ${data.summary.total_agents}`)
          addLog(`  ├─ Simulation duration: ${data.summary.simulation_hours} hours`)
          addLog(`  ├─ Initial posts: ${data.summary.initial_posts_count}`)
          addLog(`  ├─ Hot topics: ${data.summary.hot_topics_count}`)
          addLog(`  └─ Platform config: Twitter ${data.summary.has_twitter_config ? '✓' : '✗'}, Reddit ${data.summary.has_reddit_config ? '✓' : '✗'}`)
        }
        
        // Show time config details
        if (data.config.time_config) {
          const tc = data.config.time_config
          addLog(`Time config: ${tc.minutes_per_round} minutes per round, ${Math.floor((tc.total_simulation_hours * 60) / tc.minutes_per_round)} total rounds`)
        }
        
        // Show event config
        if (data.config.event_config?.narrative_direction) {
          const narrative = data.config.event_config.narrative_direction
          addLog(`Narrative direction: ${narrative.length > 50 ? narrative.substring(0, 50) + '...' : narrative}`)
        }
        
        stopConfigPolling()
        phase.value = 4
        addLog('✓ Environment setup complete, ready to start simulation')
        emit('update-status', 'completed')
      }
    }
  } catch (err) {
    console.warn('Failed to fetch Config:', err)
  }
}

const loadPreparedData = async () => {
  phase.value = 2
  addLog('Loading existing config data...')

  // Fetch Profiles one last time
  await fetchProfilesRealtime()
  addLog(`Loaded ${profiles.value.length} Agent profiles`)

  // Get config (using real-time API)
  try {
    const res = await getSimulationConfigRealtime(props.simulationId)
    if (res.success && res.data) {
      if (res.data.config_generated && res.data.config) {
        simulationConfig.value = res.data.config
        addLog('✓ Simulation config loaded successfully')
        
        // Show detailed config summary
        if (res.data.summary) {
          addLog(`  ├─ Agent count: ${res.data.summary.total_agents}`)
          addLog(`  ├─ Simulation duration: ${res.data.summary.simulation_hours} hours`)
          addLog(`  └─ Initial posts: ${res.data.summary.initial_posts_count}`)
        }
        
        addLog('✓ Environment setup complete, ready to start simulation')
        phase.value = 4
        emit('update-status', 'completed')
      } else {
        // Config data not yet generated, waiting for generation...
        addLog('Config data not yet generated, waiting for generation...')
        startConfigPolling()
      }
    }
  } catch (err) {
    addLog(`Failed to load config: ${err.message}`)
    emit('update-status', 'error')
  }
}

// Scroll log to bottom
const logContent = ref(null)
watch(() => props.systemLogs?.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})

onMounted(() => {
  // Auto start preparation process
  if (props.simulationId) {
    addLog('Step2 Environment Setup initialized')
    startPrepareSimulation()
  }
})

onUnmounted(() => {
  stopPolling()
  stopProfilesPolling()
  stopConfigPolling()
})
</script>

<style src="../../styles/Step2EnvSetup.css"></style>
