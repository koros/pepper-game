import numpy as np

# ---------------------------------
# POMDP Model START
# ---------------------------------

class PepperPOMDP:
    """POMDP model for Pepper's behavior in the MasterMind game."""
    def __init__(self):
        """Initialize the POMDP model with states, actions, observations, and transition/observation models."""
        self.states = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        self.actions = ["act_wait", "act_offerHint"]
        self.observations = ["obs_moveTime", "obs_hintAccepted"]
        self.belief = [0.30, 0.40, 0.30]  # Initial belief over states
        self._initialize_transition_model()
        self._initialize_observation_model()
        self._initialize_reward_model()
    
    def _initialize_transition_model(self):
        """Initialize the transition model T(s'|s,a) for the POMDP."""
        self.T = {
            # Transition probabilities for each action and state

            # For "act_wait", waiting for too long will lower trust
            "act_wait": np.array([          [0.80, 0.15, 0.05],     # from LOW 
                                            [0.15, 0.75, 0.10],     # from MEDIUM
                                            [0.10, 0.30, 0.60]]),   # from HIGH

            # For "act_offerHint", offering a hint instead of giving one will raise trust
            "act_offerHint": np.array([     [0.70, 0.25, 0.05],     # from LOW 
                                            [0.15, 0.45, 0.40],     # from MEDIUM
                                            [0.05, 0.15, 0.80]]),   # from HIGH

            # For "act_askToMoveCups", asking to move cups will lower trust as it shows incompetence
            "act_askToMoveCups": np.array([ [0.90, 0.08, 0.02],     # from LOW 
                                            [0.20, 0.60, 0.20],     # from MEDIUM
                                            [0.10, 0.30, 0.60]]),   # from HIGH
        }
        return

    def _initialize_observation_model(self):
        """Initialize the observation model O(o|s,a) for the POMDP."""
        self.O = {
            # Observation probabilities for each action and state

            # For "act_wait", the observation of move time is more likely to be fast if trust is low, and slow if trust is high
            "act_wait": np.array([      [0.80, 0.20],     # P(fast move | LOW), P(slow move | LOW)
                                        [0.50, 0.50],     # P(fast move | MEDIUM), P(slow move | MEDIUM)
                                        [0.20, 0.80]]),   # P(fast move | HIGH), P(slow move | HIGH)

            # For "act_offerHint", the observation of hint acceptance is more likely if trust is high, and more likely to be rejected if trust is low
            "act_offerHint": np.array([ [0.05, 0.95],     # P(hint accepted | LOW), P(hint rejected | LOW)
                                        [0.40, 0.60],     # P(hint accepted | MEDIUM), P(hint rejected | MEDIUM)
                                        [0.90, 0.10]]),   # P(hint accepted | HIGH), P(hint rejected | HIGH)
            
        }
        return
    
    def update_belief(self, action, observation):
        """Update the belief state based on the taken action and received observation."""
        
        # Prediction step: calculate the predicted belief after taking the action
        # Predicted belief = Sum of P(s'|s,a) * current belief(s)
        predicted_belief = np.dot(self.T[action].T, self.belief)


        if action in self.O:
            likelihood = self.O[action][:, observation]  # P(o|s',a) for each state s'
        else:            # If the action has no defined observation model, assume uniform likelihood
            likelihood = np.ones(len(self.states))

        new_belief = likelihood * predicted_belief  # P(o|s',a) * P(s'|a)

        self.belief = new_belief / np.sum(new_belief)  # Normalize the belief
        print("Updated belief:", self.belief)
        
        return self.belief
    
    def _initialize_reward_model(self):
        """Initialize the reward model R(s,a) for the POMDP."""
        
        self.state_rewards = {
            "LOW": -10,     # Negative to deincentivize low trust
            "MEDIUM": 20,   # High to incentivize balanced trust
            "HIGH": 5       # Low to deincentivize over-reliance on the robot
        }

        self.R_map = {
            "LOW": {
                "act_wait": -15,       # Penalize waiting when they don't trust us
                "act_offerHint": 10    # Encourage helping to build rapport
            },
            "MEDIUM": {
                "act_wait": 20,        # Highest reward: the "Sweet Spot"
                "act_offerHint": 5     # Minor reward: keep them engaged
            },
            "HIGH": {
                "act_wait": 10,        # Encourage independence (Wait > Hint)
                "act_offerHint": -10   # Discourage over-reliance
            }
        }
        return
    
    def get_reward(self, state_idx, action):
        """Retrieves R(s, a) from the state-action mapping."""
        # Map index to state name (LOW, MEDIUM, or HIGH)
        state_name = [name for name, idx in self.states.items() if idx == state_idx][0]
        
        # Return the specific reward for this state and action
        return self.R_map[state_name].get(action, 0)

    def select_action(self): 
        action_values = {}
        for action in self.actions:
            # EV = Sum of (Belief * Reward)
            expected_value = sum(self.belief[i] * self.get_reward(i, action) 
                                 for i in range(len(self.belief)))
            action_values[action] = expected_value
        
        return max(action_values, key=action_values.get), action_values
# ----------------------------------
# POMDP Model END
# ----------------------------------