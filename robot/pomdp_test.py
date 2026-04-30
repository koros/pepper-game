from pepper_pomdp import PepperPOMDP

if __name__ == "__main__":
    
    pomdp = PepperPOMDP()
    exit_test = False
    belief_delta = []
    # Set initial belief
    pomdp.belief = [0.30, 0.40, 0.30]
    print("States:", pomdp.states)
    print("Initial belief:", pomdp.belief)
    print("Actions:", pomdp.actions)
    print("Observations:", pomdp.observations)

    # Simulate performing an action and receiving an observation in loop
    while not exit_test:
        initial_belief = pomdp.belief.copy()  # Store initial belief before update
        action = input("Enter an action (0: act_wait, 1: act_offerHint, 2: act_askToMoveCups, 3: exit): ").strip()
        if action == "0":
            #  Act_wait: Simulate action with a short guess (observation 0) or a long guess (observation 1)
            
            pomdp.update_belief("act_wait", int(input("Enter observation (0: short guess, 1: long guess): ")))
        elif action == "1":
            pomdp.update_belief("act_offerHint", int(input("Enter observation (0: hint accepted, 1: hint rejected): ")))
        elif action == "2":
            pomdp.update_belief("act_askToMoveCups")
        elif action == "3":
            exit_test = True
            print("Exiting test.")
        else:
            print("Invalid action. Please enter 0, 1, 2, or 3.")
            continue
        # print change in belief for debugging
        # print("Updated belief:", pomdp.belief)
        # for i in range(len(pomdp.belief)):
        #     belief_delta.append(pomdp.belief[i] - initial_belief[i])
        # print("Belief change:", belief_delta)
        print("Current State: ", max(pomdp.states, key=lambda s: pomdp.belief[pomdp.states[s]]))  # Print the most likely state
    