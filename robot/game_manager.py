import pepper_pomdp as pepper
import compvis
import time

class GameManager:
    def __init__(self):
        self.pomdp = pepper.PepperPOMDP()
        self.not_round_over = True
        self.not_game_over = True
        self.user_speech = ""
        self.cups_order_bottom = []
        self.cups_order_top = []
        self.correct_cups = 0

        self.guess_time_start = 0
        self.guess_time_running = 0
        self.slow_move_reported = False
        self.LONG_GUESS_THRESHOLD = 15  # Threshold in seconds to consider a guess as 'long'
        self.obs_long_guess = False
        self.offer_guess_cooldown = 0
        return

    def check_cup_count(self):
        """Check if Pepper is seeing the correct number of cups."""
        # Check that there are 4 cups in the top row
        if len(self.cups_order_top) != 4:
            print("Pepper: I can't see the correct number of cups. Please make sure there are 4 cups in the top row.")
            return False
        else:
            print("Pepper: I can see the correct number of cups. Thank you!")
            return True
    
    def perform_offerHint(self):
        """Perform the action of offering a hint."""
        self.offer_guess_cooldown = 10
        # 1. Pepper asks if the user wants a hint
        # TODO: Replace this with actual speech output from Pepper
        print("Pepper: Would you like a hint?")
        # 2. User responds
        # TODO: Replace this with actual user response from Pepper's speech recognition
        user_response = input("User (yes/no): ").strip().lower()
        # 3. Update the POMDP belief based on the user's response
        if user_response == "yes":
            print("Pepper: Here is your hint!")
            return 0  # Return observation for hint accepted
        else:
            print("Pepper: No hint for you then!")
            return 1  # Return observation for hint rejected
    
    def perform_askToMoveCups(self):
        """Perform the action of asking the user to move cups."""
        # 1. Pepper asks the user to move the cups
        # TODO: Replace this with actual speech output from Pepper
        print("Pepper: Please move the cups.")
        # 2. User responds
        # TODO: Replace this with actual user response from Pepper's speech recognition
        input("User (done/continue): ").strip().lower()
        # 3. Update the POMDP belief based on the user's response
        self.pomdp.update_belief("act_askToMoveCups")
        return

    def perform_giveFeedback(self):
        """Perform the action of giving feedback."""
        self.correct_cups = 0
        while True:
            # 1. Pepper checks that it is seeing the correct number of cups
            self.cups_order_top = compvis.read_cups_top()  # Read the top row of cups
            if not self.check_cup_count():
                self.perform_askToMoveCups()  # If Pepper can't see the correct number of cups, ask the user to move them
                continue  # Check again after the user has moved the cups
            else:
                # 2. Pepper counts how many cups are correct in the user's guess and gives feedback
                for i in range(4):
                    if self.cups_order_top[i] == self.cups_order_bottom[i]:
                        self.correct_cups += 1
                print(f"Pepper: You have {self.correct_cups} cups in the correct position.")
                break
        return

    def perform_wait(self):
        """Perform the action of waiting."""
        print("Pepper: Waiting...")
        # 1. Pepper updates guess time and checks if it exceeds the threshold for a long guess
        self.guess_time_running = time.time() - self.guess_time_start
        if self.guess_time_running > self.LONG_GUESS_THRESHOLD and not self.obs_long_guess:
            self.obs_long_guess = True  # Set flag to indicate that a long guess has been observed
            self.pomdp.update_belief("act_wait", 1)  # Update belief with observation of a long guess
        else:
            return  # No belief update as guess has not exceeded the long guess threshold
        # TODO: Implement the waiting action with a delay
        return

    def run_introduction(self):

        cups_not_arranged = True
        # 1. Have you played before?
        self.user_speech = input("Pepper: Have you played MasterMind before? (yes/no): ").strip().lower()
        
        if self.user_speech == "no":
            print("Pepper: Let me explain the rules of the game...")
        else:
            print("Pepper: Great! I won't explain the rules.")
        
        # 2. Have the hidden cups been arranged?

        while cups_not_arranged:
            self.user_speech = input("Pepper: Have the hidden cups been arranged? (yes/no): ").strip().lower()
            if self.user_speech == "no":
                print("Pepper: Please arrange the hidden cups.")
                continue
            elif self.user_speech == "yes":
                print("Pepper: Thank you for arranging the cups.")
            # 3. Read the cups (both rows) and compare
            self.cups_order_bottom = compvis.read_cups_bottom()
            self.cups_order_top = compvis.read_cups_top()
            if self.cups_order_bottom == self.cups_order_top:
                print("Pepper: The sequences are already matched. Please arrange the cups so that the top and bottom rows are different.")
            else:
                print("Pepper: Cup sequences are different. We can start the game!")
                cups_not_arranged = False
        return True

    def run_conclusion(self):
        # 1. Pepper congratulates the user for winning the game
        print("Pepper: Congratulations! You have won the game.")
        # 2. Pepper asks if the user wants to play again
        self.user_speech = input("Pepper: Would you like to play again? (yes/no): ").strip().lower()
        if self.user_speech == "yes":
            self.not_round_over = True  # Reset round over flag for the next round
            self.correct_cups = 0  # Reset correct cups count for the next round
            print("Pepper: Great! Let's play again.")
        else:
            self.not_game_over = False  # End the game if the user does not want to play again
            print("Pepper: Thank you for playing! Goodbye!")

    def perform_pomdp_action(self, action):
        """Perform the specified action and update the POMDP belief accordingly."""
        if action == "act_offerHint" and self.offer_guess_cooldown == 0:
            self.perform_offerHint()
        elif action == "act_wait" or self.offer_guess_cooldown > 0:
            self.perform_wait()
        else:
            self.perform_wait()  # Default to waiting if the action is not recognized or if offerHint is on cooldown
            print(f"Unknown action: {action}")

        if self.offer_guess_cooldown > 0:
            self.offer_guess_cooldown -= 1  # Decrease cooldown for offering a hint 
        return

    def run_game_round(self):
    # Start round
        while self.not_round_over:
    # Start timer for the user's guess
            self.guess_time_start = time.time()
    # Keep making decisions and performing actions until the user says "check my guess"
            while self.user_speech != "check my guess": 
    # Pepper selects an action based on the current belief state
                best_action, action_weights = self.pomdp.select_action()
                print("Action weights:", action_weights)
    # Pepper performs the selected action, observes, and then updates its belief state accordingly
                self.perform_pomdp_action(best_action)
                time.sleep(1)  # Simulate time taken for Pepper to perform the action
                # 3. Listen for user speech input
                self.user_speech = input("User: ").strip().lower()
            # Update belief for short guess
            self.pomdp.update_belief("act_wait", 0)  # Update belief with observation of a short guess
            self.perform_giveFeedback()  # After the user says "check my guess", Pepper gives feedback on the guess
            self.user_speech = ""  # Reset user speech for the next round of guessing
            if self.correct_cups == 4:
                self.not_round_over = False  # End the round if the user has guessed all cups correctly

    def run_game(self):
        # Game Pipeline

        self.run_introduction()

        while self.not_game_over:
            self.run_game_round()
            self.run_conclusion()
        pass